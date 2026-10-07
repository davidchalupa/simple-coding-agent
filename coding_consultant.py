import sys
import os
import json
import re

from pathlib import Path

from common.input_handler import get_user_prompt
from common.output_handler import stream_agent_response
from common.guardrail_tools import check_context_guardrail
from common.context_hygiene_utils import render_token_footer
from common.context_hygiene_utils import render_token_footer

from consultant.system_prompt_builder import build_consultant_system_prompt, build_diagnose_system_prompt
from consultant.guardrail_tools import sanitize_response, is_pure_load_request
from consultant.model_switcher import ModelSwitcher
from consultant.tool_parser import extract_tool_requests
from consultant.tool_processor import process_tool_requests

from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY

MAX_ANSWERING_VIOLATIONS_PER_TURN = 3
MODE_RE = re.compile(r'^/mode\b\s*(code|think)', re.IGNORECASE)


class ConsultantState:
    def __init__(self, parsed_args):
        self.messages = []
        self.session_cwd = os.getcwd()
        self.consult_read_cache = {}
        self.forbidden_tools = frozenset({"write_file", "append_file", "patch_file", "replace_lines"})
        self.max_tool_calls_per_turn = 20

        self.primary_model_key = parsed_args["model"]
        self.reasoning_model_key = parsed_args["reasoning_model"]
        self.kv_quantization_type = parsed_args["kv_quantization_type"]
        self.models_dir = Path(__file__).resolve().parent / "models"

        # Tracks the active state: "code" (default) or "think"
        self.active_mode = "code"

        self.expect_plain_text = False
        self.answering_violations = 0
        self.last_directive = None


parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
state = ConsultantState(parsed_args)


def summarize_loaded_targets(tool_requests):
    parts = []
    for req in tool_requests:
        name = req.get("name")
        args = req.get("args", {}) or {}
        filepath = args.get("filepath")
        symbol = args.get("symbol_name")
        dirpath = args.get("dir_path")
        if name == "read_symbol" and filepath and symbol:
            parts.append(f"`{symbol}` from `{os.path.basename(filepath)}`")
        elif filepath:
            parts.append(f"`{os.path.basename(filepath)}`")
        elif dirpath:
            parts.append(f"the directory listing for `{dirpath}`")

    seen = set()
    unique_parts = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique_parts.append(p)

    return ", ".join(unique_parts) if unique_parts else "the requested content"


def handle_user_input(state, user_input, system_prompt):
    if user_input == "/quit":
        print("Exiting. Goodbye!")
        sys.exit(0)

    if user_input == "/clear":
        state.messages = [{"role": "system", "content": system_prompt}]
        state.session_cwd = os.getcwd()
        state.consult_read_cache = {}
        state.expect_plain_text = False
        state.answering_violations = 0
        state.last_directive = None
        print("🧹 Memory and environment completely cleared!")
        return True

    if user_input == "/cancel":
        print("❌ Current draft discarded.")
        return True

    if not user_input:
        return True

    return False


def main(state):
    coder_system_prompt = build_consultant_system_prompt()
    reasoner_system_prompt = build_diagnose_system_prompt()
    switcher = ModelSwitcher(state.kv_quantization_type, state.models_dir)
    switcher.load(state.primary_model_key)

    state.messages = [{"role": "system", "content": coder_system_prompt}]

    print(f"\n🔍 [Coding Consultant] {switcher.display_name} loaded. Read-only — write tools are disabled.")
    if state.reasoning_model_key is not None:
        reasoning_display = MODEL_REGISTRY[state.reasoning_model_key]["display_name"]
        print(
            f"💡 Multi-Model mode enabled: Type '/mode think' to switch to {reasoning_display}, and '/mode code' to switch back.")
    print()

    while True:
        user_input = get_user_prompt()

        if handle_user_input(state, user_input, coder_system_prompt):
            continue

        # --- Handle Mode Switching ---
        mode_match = MODE_RE.match(user_input.strip())
        if mode_match:
            new_mode = mode_match.group(1).lower()

            if new_mode == "think" and not state.reasoning_model_key:
                print("⚠️  No reasoning model configured. Operating in code mode.")
                continue

            if new_mode != state.active_mode:
                state.active_mode = new_mode
                try:
                    if state.active_mode == "think":
                        def update_system_prompt(messages, new_prompt):
                            for msg in messages:
                                if msg.get("role") == "system":
                                    msg["content"] = new_prompt
                                    return
                            update_system_prompt(state.messages, reasoner_system_prompt)

                        switcher.load(state.reasoning_model_key)
                        state.messages[0] = {"role": "system", "content": reasoner_system_prompt}
                        print(f"\n🧠 [Reasoning Mode Active] Switched to {switcher.display_name}")
                    else:
                        switcher.load(state.primary_model_key)
                        print(f"\n💻 [Coding Mode Active] Switched to {switcher.display_name}")
                except Exception as e:
                    print(f"\n❌ Failed to switch models: {e}")
            else:
                print(f"Already in {new_mode} mode.")

            # Standalone command: do not process further, wait for next user prompt
            continue

        # Ensure correct model is loaded for the active mode
        active_model_key = state.reasoning_model_key if state.active_mode == "think" else state.primary_model_key
        llm, context_window = switcher.load(active_model_key)

        state.messages.append({"role": "user", "content": user_input})

        real_tool_calls_this_turn = 0
        state.expect_plain_text = False
        state.answering_violations = 0
        state.last_directive = None

        is_reasoner = (state.active_mode == "think")

        while True:
            check_context_guardrail(state.messages, llm, context_window)

            try:
                # Dynamically assign streaming parameters based on active mode
                stream_kwargs = {
                    "agent_label": "\n🧠 [Agent]: " if is_reasoner else "\n💻 [Agent]: "
                }
                if is_reasoner:
                    stream_kwargs["repeat_penalty"] = 1.15
                    stream_kwargs["enforce_duplicate_payload_check"] = False

                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages,
                                                                                    **stream_kwargs)

                render_token_footer(state.messages, llm, context_window)

                if interrupted:
                    break

                response_content = sanitize_response(response_content)
                state.messages.append({"role": "assistant", "content": response_content})

                tool_requests = extract_tool_requests(response_content)

                if tool_requests:
                    if state.expect_plain_text:
                        state.answering_violations += 1
                        print(f"\n🛑 [Consult Guardrail] Model attempted tool call(s) in Answering Mode "
                              f"(violation {state.answering_violations}/{MAX_ANSWERING_VIOLATIONS_PER_TURN}). Blocked.")

                        if state.answering_violations >= MAX_ANSWERING_VIOLATIONS_PER_TURN:
                            reminder = state.last_directive or (
                                "Using only the context already retrieved above, answer the original "
                                "question directly."
                            )
                            state.messages.append({
                                "role": "user",
                                "content": (f"{reminder}\n\nDo not call any tools, and do not mention tools, "
                                            "rules, states, or these instructions — just give the answer "
                                            "as you would to a colleague.")
                            })
                            print("\n💬 [Consult] Forcing plain-text answer after repeated Answering Mode violations.")
                            check_context_guardrail(state.messages, llm, context_window)
                            try:
                                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages,
                                                                                                    **stream_kwargs)
                                render_token_footer(state.messages, llm, context_window)
                                if not interrupted:
                                    response_content = sanitize_response(response_content)
                                    if extract_tool_requests(response_content):
                                        fallback = (
                                            "I wasn't able to produce a plain-text answer after "
                                            "repeated attempts. The context should already be loaded "
                                            "above — try rephrasing your request."
                                        )
                                        print(f"\n[Agent]: {fallback}")
                                        state.messages.append({"role": "assistant", "content": fallback})
                                        print("\n⚠️  [Consult] Forced attempt still tried to call a tool "
                                              "— used a fallback message instead.")
                                    else:
                                        state.messages.append({"role": "assistant", "content": response_content})
                                        print("\n💬 [Consult] Agent finished (forced). Awaiting your next question.")
                            except Exception as e:
                                print(f"\n[Error during forced generation]: {e}")
                            break

                        reminder = state.last_directive or (
                            "The context you need is already available above in this conversation — "
                            "no need to fetch it again. Please answer the original question now."
                        )
                        state.messages.append({
                            "role": "user",
                            "content": (f"{reminder}\n\nNo need to fetch anything again — everything needed "
                                        "is already above. Answer now, directly and in plain text, without "
                                        "mentioning tools, rules, or these instructions.")
                        })
                        continue

                    perfect_history = "\n".join(
                        [f'<tool_call>{json.dumps(req)}</tool_call>' for req in tool_requests])
                    state.messages[-1] = {"role": "assistant", "content": perfect_history}
                else:
                    print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                    break

                combined_results, real_tool_calls_this_turn = process_tool_requests(state, response_content,
                                                                                    tool_requests,
                                                                                    real_tool_calls_this_turn)

                if combined_results.strip():
                    if is_pure_load_request(user_input):
                        # Inject the actual tool results (file contents) into the context first
                        state.messages.append({
                            "role": "user",
                            "content": f"Tool Execution Results:\n{combined_results.strip()}"
                        })

                        summary = summarize_loaded_targets(tool_requests)
                        synthesized_answer = (
                            f"Context loaded — {summary} is now available. "
                            f"What would you like to do next?"
                        )
                        print(f"\n[Agent]: {synthesized_answer}")

                        # Then append the agent's acknowledgment
                        state.messages.append({"role": "assistant", "content": synthesized_answer})

                        print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                        state.expect_plain_text = False
                        state.answering_violations = 0
                        state.last_directive = None
                        break

                    directive = (
                        f'[SYSTEM DIRECTIVE: Context retrieved. Fulfill the user\'s explicit request now: '
                        f'"{user_input}". Provide the complete solution or code draft directly. DO NOT output any tool calls.]'
                    )

                    state.messages.append({
                        "role": "user",
                        "content": f"Tool Execution Results:\n{combined_results.strip()}\n\n{directive}"
                    })
                    state.expect_plain_text = True
                    state.last_directive = directive

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
