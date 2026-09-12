import sys
import os
import json
import re

from pathlib import Path

from common.input_handler import get_user_prompt
from common.output_handler import stream_agent_response
from common.guardrail_tools import check_context_guardrail

from consultant.system_prompt_builder import build_consultant_system_prompt
from consultant.guardrail_tools import sanitize_response, is_pure_load_request
from consultant.model_switcher import ModelSwitcher
from consultant.tool_parser import extract_tool_requests
from consultant.tool_processor import process_tool_requests
from consultant.diagnose_tools import run_diagnose_turn

from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY


MAX_STATE2_VIOLATIONS_PER_TURN = 3
DIAGNOSE_RE = re.compile(r'^/diagnose\b\s*(.*)', re.IGNORECASE | re.DOTALL)

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

        # Tracks whether we're in STATE 2 (plain-text-only) of the state machine.
        # Enforced in code rather than relying solely on the prompt, since some
        # models (e.g. DeepSeek-R1-Distill) don't reliably self-enforce this.
        self.expect_plain_text = False
        self.state2_violations = 0
        self.last_directive = None  # the specific STATE 2 directive currently in force


parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
state = ConsultantState(parsed_args)


def summarize_loaded_targets(tool_requests):
    """Human-readable summary of what a round of tool calls just loaded, used
    to synthesize a deterministic acknowledgment for pure-load requests
    instead of asking the model to generate one (see is_pure_load_request)."""
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
        state.state2_violations = 0
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
    system_prompt = build_consultant_system_prompt()
    switcher = ModelSwitcher(state.kv_quantization_type, state.models_dir)
    switcher.load(state.primary_model_key)

    state.messages = [{"role": "system", "content": system_prompt}]

    print(f"\n🔍 [Coding Consultant] {switcher.display_name} loaded. Read-only — write tools are disabled.")
    if state.reasoning_model_key is not None:
        reasoning_display = MODEL_REGISTRY[state.reasoning_model_key]["display_name"]
        print(f"🧠 Diagnose mode available: prefix a question with '/diagnose' to reason "
              f"with {reasoning_display} over gathered context.")
    print()

    while True:
        user_input = get_user_prompt()

        if handle_user_input(state, user_input, system_prompt):
            continue

        diagnose_match = DIAGNOSE_RE.match(user_input)
        if diagnose_match:
            question = diagnose_match.group(1).strip()
            if not question:
                print("⚠️  Usage: /diagnose <your question> (question may be on the same line or below it)")
                continue
            run_diagnose_turn(state, switcher, question)
            continue

        llm, context_window = switcher.load(state.primary_model_key)

        state.messages.append({"role": "user", "content": user_input})

        real_tool_calls_this_turn = 0
        state.expect_plain_text = False
        state.state2_violations = 0
        state.last_directive = None

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
                            print("\n💬 [Consult] Forcing plain-text answer after repeated STATE 2 violations.")
                            check_context_guardrail(state.messages, llm, context_window)
                            try:
                                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages)
                                if not interrupted:
                                    response_content = sanitize_response(response_content)
                                    if extract_tool_requests(response_content):
                                        # Never let a raw tool-call string become the
                                        # visible "answer" — even the forced attempt
                                        # tried to call a tool again.
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
                    state.messages.append({"role": "assistant", "content": perfect_history})
                else:
                    print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                    break

                combined_results, real_tool_calls_this_turn = process_tool_requests(state, response_content,
                                                                                    tool_requests,
                                                                                    real_tool_calls_this_turn)

                if combined_results.strip():
                    if is_pure_load_request(user_input):
                        # Deterministic case: we already know exactly what
                        # the acknowledgment should say, so synthesize it
                        # directly instead of asking the model to produce
                        # one. This is the fix for a real pathology: on a
                        # pure "load X into context" turn, Qwen would often
                        # just re-emit the same tool call instead of
                        # acknowledging, sometimes even after being forced —
                        # skipping generation for this narrow, boilerplate
                        # case removes that failure mode entirely rather
                        # than just reducing it.
                        summary = summarize_loaded_targets(tool_requests)
                        synthesized_answer = (
                            f"Context loaded — {summary} is now available. "
                            f"What would you like to do next?"
                        )
                        print(f"\n[Agent]: {synthesized_answer}")
                        state.messages.append({"role": "assistant", "content": synthesized_answer})
                        print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                        state.expect_plain_text = False
                        state.state2_violations = 0
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
