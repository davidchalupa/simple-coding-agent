import sys
import os
import json
import re
import shutil
import psutil

import llama_cpp
from llama_cpp import Llama
from pathlib import Path

import common.llm_init
from common.llm_init import initialize_agent, llm, CONTEXT_WINDOW

from coding_agent.welcome_banner import display_welcome_banner
from coding_agent.input_handler import get_user_prompt
from coding_agent.tool_definitions import read_file, extract_code_blocks
from coding_agent.execute_tool import execute_tool
from coding_agent.system_prompt_builder import build_system_prompt
from coding_agent.native_helpers import get_repo_structure, generate_requirements_native, gather_deep_context, \
    gather_deep_context_ast
from coding_agent.self_verification import find_last_code_block, run_self_verification
from coding_agent.guardrail_tools import stream_agent_response, handle_ast_extraction, verify_sandbox_health
from coding_agent import hidden_readme_prompt_builder
from coding_agent import file_splitter
from coding_agent import payload_parser
from cli import parse_cli_arguments
# the agent currently supports: Qwen2.5-Coder-7B-Instruct-Q4_K_M, Hermes-3-Llama-3.1-8B.Q4_K_M
from model_registry import MODEL_REGISTRY

parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())

ALLOW_PATCH = parsed_args["allow_patch"]
FORCE_TESTING = parsed_args["force_testing"]
SELF_VERIFY_PY_WRITES = parsed_args["self_verify_py_writes"]

active_config = MODEL_REGISTRY[parsed_args["model"]]

target_path = Path(__file__).resolve().parent / "models" / active_config["filename"]
loaded_model_name = active_config["display_name"]
CONTEXT_WINDOW = active_config["max_context"]

# Global State Placeholders
messages = []
session_cwd = os.getcwd()

# --- SANDBOX STATE TRACKING ---
is_split_mode = False
is_execute_mode = False  # Track if we are using AST interception
original_split_file = None
sandbox_directory = None
automated_followup = None  # Buffer for system-generated prompt injections
has_prompted_for_tests = False


def main():
    global messages, session_cwd, is_split_mode, is_execute_mode, original_split_file, sandbox_directory, automated_followup, has_prompted_for_tests

    SYSTEM_PROMPT = build_system_prompt()
    agent = initialize_agent(target_path, loaded_model_name, active_config)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    llm = common.llm_init.llm

    display_welcome_banner(loaded_model_name, ALLOW_PATCH)

    # 6. Main Agent Loop
    while True:
        user_input = ""

        # Check if we have an automated follow-up prompt queued
        if automated_followup:
            user_input = automated_followup
            automated_followup = None
            print(f"\n[Automated User]: {user_input}")
        else:
            # smart input handler
            user_input = get_user_prompt()

            if user_input == "/quit":
                print("Exiting. Goodbye!")
                sys.exit(0)

            if user_input == "/clear":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                session_cwd = os.getcwd()
                is_split_mode = False
                is_execute_mode = False
                original_split_file = None
                sandbox_directory = None
                automated_followup = None
                has_prompted_for_tests = False
                print("🧹 Memory and environment completely cleared!")
                continue

            if user_input == "/cancel":
                print("❌ Current draft discarded.")
                continue

        # Restart loop if no input was gathered
        if not user_input:
            continue

        # --- MACRO: /requirements ---
        if user_input.startswith("/requirements"):
            no_version_flag = "--no-version" in user_input
            cleaned_input = user_input.replace("--no-version", "").strip()
            parts = cleaned_input.split(" ", 1)
            target_dir = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "."

            abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
            session_cwd = abs_target_dir

            if not os.path.isdir(abs_target_dir):
                print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
                continue

            print(f"\n⚠️  MANUAL OVERRIDE: Generate requirements.txt natively? (No versions: {no_version_flag})")
            approval = input("Allow this action? (y/n): ").strip().lower()

            if approval == 'y':
                tool_result = generate_requirements_native(abs_target_dir, no_version=no_version_flag)
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",
                     "content": f"System Alert: User manually ran /requirements for '{abs_target_dir}'. Result: {tool_result}. Briefly acknowledge completion."}
                ]
            else:
                print("🛑 Action blocked.")
                continue

        # --- MACRO: /readme ---
        elif user_input.startswith("/readme"):
            # 1. Split input into whole-word tokens to avoid substring match bugs
            tokens = user_input.split()

            deep_focus = "--deep" in tokens or "-d" in tokens
            deep_ast_focus = "--deep-ast" in tokens

            # 2. Filter out the command and the flags
            flags_to_remove = {"/readme", "--deep", "-d", "--deep-ast"}
            path_tokens = [t for t in tokens if t not in flags_to_remove]

            # 3. Join the remaining tokens to form the path (handles unquoted paths with spaces)
            target_dir = " ".join(path_tokens) if path_tokens else "."

            abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
            session_cwd = abs_target_dir

            if not os.path.isdir(abs_target_dir):
                print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
                continue

            print(f"\n🔍 Pre-computing repository structure for {abs_target_dir}...")

            repo_tree = get_repo_structure(abs_target_dir)
            readme_path = os.path.join(abs_target_dir, "README.md")

            if os.path.exists(readme_path):
                existing_readme = read_file(readme_path, start_line=1, max_lines=1000)
                print("   [Notice] Existing README.md found. Forcing structural analysis.")
            else:
                existing_readme = "No existing README.md found. Create from scratch."
                print("   [Notice] No README.md found. Agent will draft a new one.")

            # Deep Mode Trigger Interceptor
            code_summary = None
            cli_help = None
            if deep_ast_focus:
                print(
                    "👀 [Mode Change] Experimental Dispatcher: Extracting AST interfaces and auto-routing based on size...")
                code_summary, cli_help = gather_deep_context_ast(abs_target_dir)
            elif deep_focus:
                print("👀 [Mode Change] Deep Scan: Extracting script code segments and querying CLI help hooks...")
                code_summary, cli_help = gather_deep_context(abs_target_dir)

            strategy_steps = hidden_readme_prompt_builder.build_strategy_steps(
                readme_path, ALLOW_PATCH,
                deep_focus=(deep_focus or deep_ast_focus)
            )

            hidden_readme_prompt = hidden_readme_prompt_builder.build_hidden_readme_prompt(
                abs_target_dir, repo_tree, existing_readme, strategy_steps, code_summary=code_summary,
                cli_help=cli_help
            )

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": hidden_readme_prompt}
            ]

        # --- MACRO: /split ---
        elif user_input.startswith("/split"):
            execute_mode = "--execute" in user_input
            cleaned_args = user_input.replace("--execute", "").strip().split(" ", 1)

            if len(cleaned_args) < 2 or not cleaned_args[1].strip():
                print("❌ Error: You must provide a filepath. Usage: /split [--execute] [filepath]")
                continue

            target_file = cleaned_args[1].strip()
            abs_target_file = os.path.abspath(os.path.expanduser(target_file))

            if not os.path.isfile(abs_target_file):
                print(f"❌ Error: Target file '{abs_target_file}' does not exist.")
                continue

            if execute_mode:
                print(
                    f"\n⚠️  [WARNING] Execution Mode Active: System will use AST natively based on LLM JSON mapping.")
                print("   This isolates the agent from hallucinating logic blocks.")
                print(f"🔍 Initializing Sandbox and Parsing AST structure for {abs_target_file}...")
            else:
                print(f"\n🔍 Initializing Sandbox (Advisor Mode) for {abs_target_file}...")

            # 1. Setup sandbox tracking
            _, sandbox_directory = file_splitter.setup_refactor_sandbox(abs_target_file)
            original_split_file = abs_target_file
            is_split_mode = True
            is_execute_mode = execute_mode

            # 2. Divert agent's current working directory to the sandbox!
            session_cwd = sandbox_directory

            # Pass the flag to the prompt builder
            split_prompt = file_splitter.build_split_prompt(abs_target_file, session_cwd, execute_mode=execute_mode)

            if not execute_mode:
                split_prompt += "\n\nFormat your plan now. Do not write file contents yet. Wait for confirmation."

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": split_prompt}
            ]

        else:
            # Standard execution or continuation of sandbox mode
            messages.append({"role": "user", "content": user_input})

        # Internal Agent Execution Loop
        file_was_modified = False  # Track if any files change during this cycle
        last_tool_call_signature = None  # <-- Track the last tool run
        consecutive_errors = 0  # <-- Track infinite loop traps
        consecutive_lint_failures = 0  # <-- Track repeated self-verification failures on the same turn
        last_verification_failure = None  # <-- Track {filepath, content, error} of the last failed lint, to detect stale-fix reuse

        # Loop Guardrail: track recent signatures, not just the immediately previous one
        recent_tool_signatures = []

        def check_context_guardrail(messages, llm, limit):
            """Calculates tokens and warns on memory overload."""
            try:
                tokens = sum(len(llm.tokenize(m["content"].encode('utf-8'))) + 10 for m in messages)
                if tokens > limit:
                    print(
                        f"\n🚨 [MEMORY OVERLOAD]: Prompt size is {tokens} tokens (Limit: {limit}).\n   The agent will likely hallucinate... Consider using '/clear' or '--deep-ast'.")
                elif tokens > int(limit * 0.85):
                    print(
                        f"\n⚠️  [MEMORY WARNING]: Approaching context limit ({tokens}/{limit} tokens, {(tokens / limit) * 100:.1f}%).")
            except Exception:
                pass

        while True:
            check_context_guardrail(messages, llm, CONTEXT_WINDOW)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, messages)
                if interrupted: break

                # --- AST EXTRACTION INTERCEPTOR ---
                if is_split_mode and is_execute_mode and "```json" in response_content:
                    handled, alert = handle_ast_extraction(response_content, original_split_file, sandbox_directory)
                    if handled:
                        messages.append({"role": "user", "content": alert})
                        is_execute_mode = False if "successfully executed" in alert else is_execute_mode
                        continue

                # 🚨 FIX: Check for tool calls FIRST. Never let "Refactor Phase Complete"
                # short-circuit a turn that also contains a tool call.
                tool_request = payload_parser.extract_tool_call(response_content, allow_patch=ALLOW_PATCH)

                if not tool_request:
                    # --- SANDBOX GUARDRAIL (Only runs if NO tool was called) ---
                    if is_split_mode and any(
                            x in response_content.lower() for x in ["refactor phase complete", "task complete"]):
                        passed, report = verify_sandbox_health(original_split_file, sandbox_directory, messages)
                        if passed:
                            print(f"✅ Sandbox passed! Staged in: {sandbox_directory}")
                            if input("Promote to production? (y/n): ").strip().lower() == 'y':
                                target_dir = os.path.dirname(original_split_file)
                                for item in os.listdir(sandbox_directory):
                                    if not item.startswith('.'):
                                        shutil.copy2(os.path.join(sandbox_directory, item),
                                                     os.path.join(target_dir, item))
                                print("🚀 Files successfully promoted.")
                            is_split_mode = is_execute_mode = False
                            session_cwd = os.path.dirname(original_split_file)
                            break

                        print(f"❌ Verification Failed:\n{report}")
                        messages.append({"role": "user",
                                         "content": f"System Verification Failed:\n{report}\n\nCorrect this error and output 'Refactor Phase Complete'."})
                        continue

                    # AUTOMATED FOLLOW-UP TRIGGER
                    if FORCE_TESTING and file_was_modified and not is_split_mode:
                        raw_path = tool_args.get("filepath", "") if 'tool_args' in locals() else ""
                        fn = Path(raw_path).name.lower()
                        if raw_path and fn.endswith(".py") and (
                                fn.startswith("test_") or fn.endswith("_test.py")) and not has_prompted_for_tests:
                            print("\n[System]: Automatically queuing follow-up test prompt.")
                            safe_exec = sys.executable.replace("\\", "/")
                            messages.append({"role": "user",
                                             "content": f"Great. Use `run_cmd` (e.g. `\"{safe_exec}\" -m unittest`) to verify. If a test fails, analyze if the test itself is wrong before fixing the source code."})
                            has_prompted_for_tests = True
                        else:
                            print("\n[System]: Main script written / modified.")
                    break

                tool_name = tool_request.get("name")
                tool_args = tool_request.get("args", {})

                # --- PRE-FLIGHT VALIDATION & GUARDRAILS ---
                if tool_name in ["read_file", "run_cmd"] and "<payload>" in response_content:
                    messages.append({"role": "user",
                                     "content": f"System Alert: Tool `{tool_name}` does NOT accept <payload> blocks. Retry with ONLY the JSON block."})
                    continue

                if is_truncated and tool_name == "write_file":
                    raise json.JSONDecodeError("Incomplete payload due to context limit.", "", 0)

                # Path resolution
                for key in ["filepath", "dir_path"]:
                    if key in tool_args and not os.path.isabs(tool_args[key]):
                        tool_args[key] = os.path.abspath(os.path.join(session_cwd, tool_args[key]))

                # Define standard payload key handling
                content_key = "new_content" if tool_name == "patch_file" else "content"

                # Payload recovery & Empty file guard
                if tool_name in ["write_file", "append_file", "patch_file", "replace_lines"]:
                    content_clean = re.sub(r'```[a-zA-Z]*\s*```', '', tool_args.get(content_key, '')).strip()

                    if not content_clean:
                        recovered = find_last_code_block(messages)
                        is_stale = bool(recovered and last_verification_failure and
                                        last_verification_failure.get("filepath") == tool_args.get("filepath") and
                                        recovered.strip() == last_verification_failure.get("content", "").strip())

                        if recovered and recovered.strip() and not is_stale:
                            print(f"🔧 [Recovery] Reusing last drafted code block for {tool_name}.")
                            tool_args[content_key] = recovered
                        else:
                            msg = (f"System Alert: Blocked empty {tool_name}." if not is_stale else
                                   f"System Alert: Stale-Fix Guard. You provided the SAME failing code again.\n{last_verification_failure.get('error', '')}")
                            msg += f"\nYou MUST provide the corrected code inside a <payload> block. Retry {tool_name}."

                            consecutive_errors += 1
                            if consecutive_errors >= 3:
                                print("🛑 [Circuit Breaker] Agent stuck in syntax loop. Forcing exit.")
                                break
                            messages.append({"role": "user", "content": msg})
                            continue

                # Loop Guardrail
                curr_sig = f"{tool_name}:{str(tool_args)}"
                recent_tool_signatures.append(curr_sig)
                recent_tool_signatures = recent_tool_signatures[-6:]  # keep a short rolling window

                repeat_count = recent_tool_signatures.count(curr_sig)
                if repeat_count >= 2:
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        print("🛑 [Circuit Breaker] Agent loop. Forcing turn end.")
                        break
                    messages.append({"role": "user",
                                     "content": f"System Alert: This exact tool call has been attempted {repeat_count} times "
                                                f"recently and is not succeeding. Do not repeat it verbatim — either fix the "
                                                f"underlying issue (e.g. re-check content/context requirements) or try a "
                                                f"different approach."})
                    continue

                # --- EXECUTION ---
                print(f"\n⚠️  AGENT REQUESTS EXECUTION: {tool_name}")

                target_code = tool_args.get(content_key, '')

                if tool_name in ["write_file", "append_file", "patch_file", "replace_lines"]:
                    if tool_name == "patch_file":
                        old_snip = tool_args.get('old_content', '')
                        print(
                            f"Target: {tool_args.get('filepath')}\n"
                            f"--- Replacing ---\n{old_snip}\n"
                            f"--- With ---\n{target_code}\n{'-' * 20}"
                        )
                    else:
                        line_count = target_code.count('\n') + 1 if target_code else 0
                        MAX_PREVIEW_CHARS = 5000
                        if len(target_code) > MAX_PREVIEW_CHARS:
                            shown = target_code[:MAX_PREVIEW_CHARS]
                            print(
                                f"Target: {tool_args.get('filepath')} ({line_count} lines, showing first {MAX_PREVIEW_CHARS} chars)\n{'-' * 20}\n{shown}\n...\n{'-' * 20}")
                        else:
                            print(
                                f"Target: {tool_args.get('filepath')} ({line_count} lines)\n{'-' * 20}\n{target_code}\n{'-' * 20}")
                else:
                    print(f"Arguments: {tool_args}")

                approval = input("Allow this action? (y/n/edit): ").strip().lower()
                tool_result, tool_reinforcement = "", ""

                if approval == 'y':
                    tool_result, tool_reinforcement, was_mod = execute_tool(tool_name, tool_args, is_split_mode)
                    file_was_modified = file_was_modified or was_mod
                    print(f"⚙️  Tool execution finished.")

                    # Auxiliary output of the tool_result, useful for debugging
                    MAX_RESULT_PREVIEW = 400
                    # MAX_RESULT_PREVIEW = 2000
                    if len(tool_result) > MAX_RESULT_PREVIEW:
                        preview = tool_result[:MAX_RESULT_PREVIEW]
                        print(f"   Result ({len(tool_result)} chars, truncated): {preview}...")
                    else:
                        print(f"   Result: {tool_result}")
                    # Self-Verification
                    if SELF_VERIFY_PY_WRITES and was_mod and tool_name in ["write_file", "append_file",
                                                                           "patch_file", "replace_lines"]:
                        fp = tool_args.get("filepath", "")
                        if linter_error := run_self_verification(fp):
                            consecutive_lint_failures += 1
                            print(f"🚨 [Self-Verification] FAILED on {os.path.basename(fp)}:\n{linter_error}")

                            # Track failure using dynamic content_key
                            last_verification_failure = {
                                "filepath": fp,
                                "content": tool_args.get(content_key, ""),
                                "error": linter_error
                            }

                            tool_reinforcement += f"\n\nSystem Alert: File written but syntax/import check failed:\n{linter_error}\nFix it."

                            if consecutive_lint_failures >= 3:
                                print("🛑 [Circuit Breaker] Repeated lint failures. Forcing turn end.")
                                messages.append(
                                    {"role": "user", "content": f"Tool Result:\n{tool_result}{tool_reinforcement}"})
                                break
                        else:
                            if consecutive_lint_failures > 0: print(f"✅ {os.path.basename(fp)} now passes checks.")
                            consecutive_lint_failures, last_verification_failure = 0, None

                elif approval == 'edit':
                    tool_result = f"User denied and provided feedback: {input('Feedback: ')}"
                else:
                    tool_result = "User denied permission."
                    print("🛑 Action blocked.")

                messages.append(
                    {"role": "user", "content": f"Tool Execution Result:\n{tool_result}{tool_reinforcement}"})

            # potential recovery from misformatted JSONs
            except json.JSONDecodeError as e:
                print(f"\n❌ [Parser Interceptor] Caught JSON error, feeding back to agent...")
                error_msg = (
                    f"⚠️ ACTION FAILED: Your tool call was NOT executed because the JSON is invalid.\n"
                    f"JSON Error: {str(e)}\n\n"
                    f"Hint: In JSON, you cannot escape single quotes like \\'. To put a literal backslash "
                    f"and a quote in Python code via JSON, you must double-escape the backslash: \\\\', "
                    f"or avoid illegal JSON escape sequences. Please fix your JSON and try again."
                )
                messages.append({"role": "user", "content": error_msg})

                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print("🛑 [Circuit Breaker] Agent stuck in JSON formatting loop. Forcing exit.")
                    break

                continue  # CRITICAL: 'continue' lets the agent retry instantly
            # except json.JSONDecodeError as e:
            #     print(f"\n❌ [Parser Interceptor] Halted syntax loop.")
            #     messages.append({"role": "user",
            #                      "content": f"Formatting Failure: {e}\nRemember to use raw unescaped content inside <payload>."})
            #     break
            except Exception as e:
                print(f"\n[Error during generation]: {e}")
                break



if __name__ == "__main__":
    main()
