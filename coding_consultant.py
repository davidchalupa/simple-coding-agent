import sys
import os
import json
import re

from pathlib import Path

from common.llm_init import LLMInitializer

from common.input_handler import get_user_prompt
from common.execute_tool import execute_tool
from common.output_handler import stream_agent_response
from common.guardrail_tools import check_context_guardrail

from consultant.system_prompt_builder import build_consultant_system_prompt
from consultant.guardrail_tools import fuzzy_extract_tool_calls

from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY


# --- Sanitization for models that emit reasoning traces / chat-template leakage ---
# (e.g. DeepSeek-R1-Distill-Qwen). No-op for models that never produce these,
# such as Qwen2.5-Coder, so behavior there is unchanged.
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
STRAY_TOKEN_RE = re.compile(r"<\|im_start\|>\s*assistant\s*|<\|im_end\|>|<\|im_start\|>")

MAX_STATE2_VIOLATIONS_PER_TURN = 3

# Must match the tool names actually defined in build_consultant_system_prompt().
# Anything outside this set is a hallucinated tool (e.g. "load_file") and gets
# rejected the same way forbidden_tools are, rather than reaching execute_tool.
KNOWN_TOOLS = frozenset({"list_tree", "search_codebase", "read_file", "read_symbol", "run_cmd"})


def sanitize_response(response_content: str) -> str:
    """Strip reasoning blocks and leaked chat-template tokens before the
    content is parsed for tool calls or stored in message history."""
    cleaned = THINK_BLOCK_RE.sub("", response_content)
    if "<think>" in cleaned and "</think>" not in cleaned:
        # Truncated mid-thought (e.g. hit a stop condition) — drop everything
        # from the opening <think> tag onward, since it's unfinished reasoning.
        cleaned = cleaned.split("<think>")[0]
    cleaned = STRAY_TOKEN_RE.sub("", cleaned)
    return cleaned.strip()


class ConsultantState:
    def __init__(self, parsed_args):
        self.messages = []
        self.session_cwd = os.getcwd()
        self.consult_read_cache = {}
        self.forbidden_tools = frozenset({"write_file", "append_file", "patch_file", "replace_lines"})
        self.max_tool_calls_per_turn = 20

        self.active_config = MODEL_REGISTRY[parsed_args["model"]]
        self.loaded_model_name = self.active_config["display_name"]
        self.kv_quantization_type = parsed_args["kv_quantization_type"]
        self.target_path = Path(__file__).resolve().parent / "models" / self.active_config["filename"]

        # Tracks whether we're in STATE 2 (plain-text-only) of the state machine.
        # Enforced in code rather than relying solely on the prompt, since some
        # models (e.g. DeepSeek-R1-Distill) don't reliably self-enforce this.
        self.expect_plain_text = False
        self.state2_violations = 0


parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
state = ConsultantState(parsed_args)


def is_pure_load_request(user_input: str) -> bool:
    """Checks if the user prompt is strictly requesting to load/read context without asking a task."""
    lowered = user_input.strip().lower()
    load_keywords = ("load ", "read ", "bring ", "fetch ", "show ")
    task_keywords = ("how", "why", "change", "refactor", "modify", "add", "fix", "update", "draft", "write", "create",
                     "implement", "start")

    is_load_cmd = any(lowered.startswith(kw) or f" {kw}" in lowered for kw in load_keywords) or lowered.endswith(
        "into context")
    has_task_cmd = any(kw in lowered for kw in task_keywords) or "?" in lowered

    return is_load_cmd and not has_task_cmd


def handle_user_input(state, user_input, system_prompt):
    if user_input == "/quit":
        print("Exiting. Goodbye!")
        sys.exit(0)

    if user_input == "/clear":
        state.messages = [{"role": "system", "content": system_prompt}]
        state.session_cwd = os.getcwd()
        state.consult_read_cache = {}
        state.expect_plain_text = False
        state.state2_violations = 0
        print("🧹 Memory and environment completely cleared!")
        return True

    if user_input == "/cancel":
        print("❌ Current draft discarded.")
        return True

    if not user_input:
        return True

    return False


def extract_tool_requests(response_content):
    raw_calls = re.findall(r'<tool_call>(.*?)</tool_call>', response_content, re.DOTALL)
    tool_requests = []

    if raw_calls:
        for call in raw_calls:
            try:
                parsed = json.loads(call)
                if "name" in parsed:
                    tool_requests.append(parsed)
            except json.JSONDecodeError:
                continue
    else:
        try:
            clean_content = response_content
            if "```json" in clean_content:
                clean_content = clean_content.split("```json")[1].split("```")[0]
            elif "```" in clean_content:
                clean_content = clean_content.split("```")[1].split("```")[0]

            clean_content = clean_content.strip()
            if clean_content.startswith("[") and clean_content.endswith("]"):
                parsed_array = json.loads(clean_content)
                if isinstance(parsed_array, list):
                    for item in parsed_array:
                        if isinstance(item, dict) and "name" in item:
                            tool_requests.append(item)
        except Exception:
            pass

        if not tool_requests:
            tool_requests = fuzzy_extract_tool_calls(response_content)

    return tool_requests


def process_tool_requests(state, response_content, tool_requests, real_tool_calls_this_turn):
    combined_results = ""

    for tool_request in tool_requests:
        tool_name = tool_request.get("name")
        tool_args = tool_request.get("args", {})

        if tool_name in state.forbidden_tools:
            print(f"🛑 [Consult Guardrail] Blocked disallowed tool `{tool_name}`.")
            combined_results += f"System Alert: Tool `{tool_name}` is strictly disabled.\n\n"
            continue

        if tool_name not in KNOWN_TOOLS:
            print(f"🛑 [Consult Guardrail] Blocked unknown tool `{tool_name}` (not a real tool).")
            combined_results += (
                f"System Alert: `{tool_name}` is not a valid tool. "
                f"Valid tools are: {', '.join(sorted(KNOWN_TOOLS))}.\n\n"
            )
            continue

        if tool_name in ("read_file", "run_cmd") and "<payload>" in response_content:
            combined_results += f"System Alert: `{tool_name}` does NOT accept <payload> blocks.\n\n"
            continue

        for key in ["filepath", "dir_path"]:
            if key in tool_args and not os.path.isabs(tool_args[key]):
                tool_args[key] = os.path.abspath(os.path.join(state.session_cwd, tool_args[key]))

        cache_key = None
        if tool_name in ("read_file", "read_symbol"):
            cache_key = (
                tool_name, tool_args.get("filepath"), tool_args.get("symbol_name"),
                tool_args.get("start_line"), tool_args.get("max_lines")
            )
            if cache_key in state.consult_read_cache:
                print(
                    f"📎 [Consult Cache] Reusing cached result for {tool_name} on {tool_args.get('filepath')}.")
                combined_results += f"Result for {tool_name}:\n[Note: Content already loaded in context history]\n{state.consult_read_cache[cache_key]}\n\n"
                continue

        if real_tool_calls_this_turn >= state.max_tool_calls_per_turn:
            print("\n💬 [Consult] Tool call budget reached. Skipping remaining queued tools.")
            break

        print(f"\n⚠️  CONSULTANT REQUESTS EXECUTION: {tool_name}")
        print(f"Arguments: {tool_args}")

        approval = input("Allow this action? (y/n/edit): ").strip().lower()

        if approval == 'y':
            tool_result, tool_reinforcement, _ = execute_tool(tool_name, tool_args, is_split_mode=False)
            real_tool_calls_this_turn += 1
            print(f"⚙️  Tool execution finished.")

            MAX_RESULT_PREVIEW = 400
            if len(tool_result) > MAX_RESULT_PREVIEW:
                print(
                    f"   Result ({len(tool_result)} chars, truncated): {tool_result[:MAX_RESULT_PREVIEW]}...")
            else:
                print(f"   Result: {tool_result}")

            if cache_key:
                state.consult_read_cache[cache_key] = tool_result

            combined_results += f"Result for {tool_name}:\n{tool_result}{tool_reinforcement}\n\n"

        elif approval == 'edit':
            feedback = input('Feedback: ')
            combined_results += f"User denied {tool_name}. Feedback: {feedback}\n\n"
        else:
            print("🛑 Action blocked.")
            combined_results += f"User denied permission for {tool_name}.\n\n"

    return combined_results, real_tool_calls_this_turn


def main(state):
    system_prompt = build_consultant_system_prompt()

    initializer = LLMInitializer(state.target_path, state.loaded_model_name, state.active_config,
                                 state.kv_quantization_type)
    initializer.initialize_agent()
    context_window = initializer.CONTEXT_WINDOW
    llm = initializer.llm

    state.messages = [{"role": "system", "content": system_prompt}]

    print(f"\n🔍 [Coding Consultant] {state.loaded_model_name} loaded. Read-only — write tools are disabled.\n")

    while True:
        user_input = get_user_prompt()

        if handle_user_input(state, user_input, system_prompt):
            continue

        state.messages.append({"role": "user", "content": user_input})

        real_tool_calls_this_turn = 0
        state.expect_plain_text = False
        state.state2_violations = 0

        while True:
            check_context_guardrail(state.messages, llm, context_window)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages)
                if interrupted:
                    break

                response_content = sanitize_response(response_content)

                state.messages.append({"role": "assistant", "content": response_content})

                tool_requests = extract_tool_requests(response_content)

                if tool_requests:
                    if state.expect_plain_text:
                        state.state2_violations += 1
                        print(f"\n🛑 [Consult Guardrail] Model attempted tool call(s) in STATE 2 "
                              f"(violation {state.state2_violations}/{MAX_STATE2_VIOLATIONS_PER_TURN}). Blocked.")

                        if state.state2_violations >= MAX_STATE2_VIOLATIONS_PER_TURN:
                            state.messages.append({
                                "role": "user",
                                "content": ("Using only the context already retrieved above, answer the "
                                             "original question directly. Do not call any tools, and do not "
                                             "mention tools, rules, states, or this instruction itself — just "
                                             "give the answer as you would to a colleague.")
                            })
                            print("\n💬 [Consult] Forcing plain-text answer after repeated STATE 2 violations.")
                            # Fall through to one more generation attempt without tools honored.
                            check_context_guardrail(state.messages, llm, context_window)
                            try:
                                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages)
                                if not interrupted:
                                    response_content = sanitize_response(response_content)
                                    state.messages.append({"role": "assistant", "content": response_content})
                                    print("\n💬 [Consult] Agent finished (forced). Awaiting your next question.")
                            except Exception as e:
                                print(f"\n[Error during forced generation]: {e}")
                            break

                        state.messages.append({
                            "role": "user",
                            "content": ("The file or context you need is already available above in this "
                                         "conversation — no need to fetch it again. Please answer the "
                                         "original question now, directly and in plain text, without "
                                         "mentioning tools, rules, or these instructions.")
                        })
                        continue

                    perfect_history = "\n".join(
                        [f'<tool_call>{json.dumps(req)}</tool_call>' for req in tool_requests])
                    state.messages.append({"role": "assistant", "content": perfect_history})
                else:
                    print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                    break

                combined_results, real_tool_calls_this_turn = process_tool_requests(state, response_content,
                                                                                    tool_requests,
                                                                                    real_tool_calls_this_turn)

                if combined_results.strip():
                    if is_pure_load_request(user_input):
                        directive = (
                            "[SYSTEM DIRECTIVE: Context loaded. Acknowledge to the user that "
                            "the context is ready and ask what they would like to do next.]"
                        )
                    else:
                        directive = (
                            f'[SYSTEM DIRECTIVE: Context retrieved. Fulfill the user\'s explicit request now: '
                            f'"{user_input}". Provide the complete solution or code draft directly. DO NOT output any tool calls.]'
                        )

                    state.messages.append({
                        "role": "user",
                        "content": f"Tool Execution Results:\n{combined_results.strip()}\n\n{directive}"
                    })
                    state.expect_plain_text = True

                if real_tool_calls_this_turn >= state.max_tool_calls_per_turn:
                    state.messages.append({
                        "role": "user",
                        "content": "Tool limit reached for this turn. Provide your final answer in plain text based on the retrieved context."
                    })
                    state.expect_plain_text = True

            except json.JSONDecodeError as e:
                print(f"\n❌ [Parser Interceptor] Halted syntax loop.")
                state.messages.append({"role": "user",
                                       "content": f"Formatting Failure: {e}\nRemember to use raw unescaped content, no extra wrapping."})
                break
            except Exception as e:
                print(f"\n[Error during generation]: {e}")
                break


if __name__ == "__main__":
    main(state)
