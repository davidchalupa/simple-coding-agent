import sys
import os
import json
import re

from pathlib import Path

from common.llm_init import LLMInitializer

from consultant.system_prompt_builder import build_consultant_system_prompt
from consultant.guardrail_tools import check_context_guardrail, fuzzy_extract_tool_calls

from coding_agent.input_handler import get_user_prompt
from coding_agent.execute_tool import execute_tool
from coding_agent.guardrail_tools import stream_agent_response
from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY

# --- CLI / MODEL SETUP ---
parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
active_config = MODEL_REGISTRY[parsed_args["model"]]
disable_kv_quantization = parsed_args["disable_kv_quantization"]

target_path = Path(__file__).resolve().parent / "models" / active_config["filename"]
loaded_model_name = active_config["display_name"]

# --- GLOBAL STATE ---
# Deliberately no is_split_mode / is_execute_mode / sandbox_directory / ALLOW_PATCH /
# FORCE_TESTING / SELF_VERIFY_PY_WRITES here. This script is read-only, single-purpose,
# and none of that agent-mode machinery can ever be reached from it.
messages = []
session_cwd = os.getcwd()
consult_read_cache = {}  # {(tool_name, filepath, symbol_name, start_line, max_lines): tool_result}

# Tool names that must never be reachable from consult mode. Kept as a defense-in-depth
# guardrail even though the parser is always called with allow_patch=False here — if a
# future shared-file change to payload_parser ever starts recognizing these regardless
# of that flag, this catches it instead of silently executing a write.
WRITE_TOOLS = frozenset({"write_file", "append_file", "patch_file", "replace_lines"})

# Consult mode allows at most this many REAL tool executions per user question before
# forcing a text-only answer from whatever context already exists. Cache hits don't
# count against this (they're free), but they do feed the loop-detection signature list
# below so a model that ignores cached content still gets cut off deterministically.
# ToDo: make this tunable
MAX_TOOL_CALLS_PER_TURN = 20


def main():
    global messages, session_cwd, consult_read_cache

    SYSTEM_PROMPT = build_consultant_system_prompt()

    initializer = LLMInitializer(target_path, loaded_model_name, active_config, disable_kv_quantization)
    initializer.initialize_agent()

    CONTEXT_WINDOW = initializer.CONTEXT_WINDOW

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    llm = initializer.llm

    print(f"\n🔍 [Coding Consultant] {loaded_model_name} loaded. Read-only — write tools are disabled.\n")

    # --- Main Consultant Loop ---
    while True:
        user_input = get_user_prompt()

        if user_input == "/quit":
            print("Exiting. Goodbye!")
            sys.exit(0)

        if user_input == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            session_cwd = os.getcwd()
            consult_read_cache = {}
            print("🧹 Memory and environment completely cleared!")
            continue

        if user_input == "/cancel":
            print("❌ Current draft discarded.")
            continue

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # --- Per-turn state ---
        real_tool_calls_this_turn = 0

        # Internal Consultant Execution Loop
        while True:
            check_context_guardrail(messages, llm, CONTEXT_WINDOW)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, messages)
                if interrupted:
                    break

                # Save the model's output to history
                messages.append({"role": "assistant", "content": response_content})

                # --- 1. Regex extraction for <tool_call> tags ---
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
                    # --- 2. Fallback: Check if the model output a JSON array instead ---
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

                    # --- 3. Ultimate Fallback: Fuzzy brace-counting extraction ---
                    if not tool_requests:
                        tool_requests = fuzzy_extract_tool_calls(response_content)

                # HISTORY NORMALIZATION
                if tool_requests:
                    # Force the model's memory to look like perfect XML,
                    # even if it hallucinated markdown blocks.
                    perfect_history = "\n".join(
                        [f'<tool_call>{json.dumps(req)}</tool_call>' for req in tool_requests])
                    messages.append({"role": "assistant", "content": perfect_history})
                else:
                    # Plain text reply — nothing left to do this turn.
                    messages.append({"role": "assistant", "content": response_content})
                    print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                    break

                combined_results = ""

                # --- PROCESS all extracted tools sequentially ---
                for tool_request in tool_requests:
                    tool_name = tool_request.get("name")
                    tool_args = tool_request.get("args", {})

                    if tool_name in WRITE_TOOLS:
                        print(f"🛑 [Consult Guardrail] Blocked disallowed tool `{tool_name}`.")
                        combined_results += f"System Alert: Tool `{tool_name}` is strictly disabled.\n\n"
                        continue

                    if tool_name in ("read_file", "run_cmd") and "<payload>" in response_content:
                        combined_results += f"System Alert: `{tool_name}` does NOT accept <payload> blocks.\n\n"
                        continue

                    for key in ["filepath", "dir_path"]:
                        if key in tool_args and not os.path.isabs(tool_args[key]):
                            tool_args[key] = os.path.abspath(os.path.join(session_cwd, tool_args[key]))

                    # --- CACHE INTERCEPT ---
                    cache_key = None
                    if tool_name in ("read_file", "read_symbol"):
                        cache_key = (
                            tool_name, tool_args.get("filepath"), tool_args.get("symbol_name"),
                            tool_args.get("start_line"), tool_args.get("max_lines")
                        )
                        if cache_key in consult_read_cache:
                            print(
                                f"📎 [Consult Cache] Reusing cached result for {tool_name} on {tool_args.get('filepath')}.")
                            combined_results += f"Result for {tool_name}:\n{consult_read_cache[cache_key]}\n\n"
                            continue

                    # --- MAX CALLS CHECK ---
                    if real_tool_calls_this_turn >= MAX_TOOL_CALLS_PER_TURN:
                        print("\n💬 [Consult] Tool call budget reached. Skipping remaining queued tools.")
                        break

                    # --- EXECUTION ---
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
                            consult_read_cache[cache_key] = tool_result

                        combined_results += f"Result for {tool_name}:\n{tool_result}{tool_reinforcement}\n\n"

                    elif approval == 'edit':
                        feedback = input('Feedback: ')
                        combined_results += f"User denied {tool_name}. Feedback: {feedback}\n\n"
                    else:
                        print("🛑 Action blocked.")
                        combined_results += f"User denied permission for {tool_name}.\n\n"

                # --- FEEDBACK & LOOP CONTINUATION ---
                if combined_results.strip():
                    messages.append({
                        "role": "user",
                        "content": f"Tool Execution Results:\n{combined_results.strip()}\n\n[SYSTEM DIRECTIVE: Context loaded. If the user's initial request is fully satisfied, reply with 'Context loaded. What would you like to know?' and DO NOT output further tool calls.]"
                    })

                # If we hit the budget, add a forceful prompt to make the model stop tooling and answer
                if real_tool_calls_this_turn >= MAX_TOOL_CALLS_PER_TURN:
                    messages.append({
                        "role": "user",
                        "content": "Tool limit reached for this turn. Provide your final answer in plain text based on the retrieved context."
                    })

                # The loop cycles back up to stream_agent_response() here
                # so the LLM can generate text (or more tool calls) based on the combined_results!

            except json.JSONDecodeError as e:
                print(f"\n❌ [Parser Interceptor] Halted syntax loop.")
                messages.append({"role": "user",
                                 "content": f"Formatting Failure: {e}\nRemember to use raw unescaped content, no extra wrapping."})
                break
            except Exception as e:
                print(f"\n[Error during generation]: {e}")
                break


if __name__ == "__main__":
    main()
