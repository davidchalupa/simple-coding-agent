import os
import json

from pathlib import Path

from common.llm_init import LLMInitializer
from common.input_handler import get_user_prompt
from common.execute_tool import execute_tool
from common.guardrail_tools import check_context_guardrail
from common.output_handler import stream_agent_response
from common.context_hygiene_utils import render_token_footer
from common.display_tools import format_tool_args_for_display

from coding_agent.welcome_banner import display_welcome_banner
from coding_agent import system_prompt_builder_light, system_prompt_builder
from coding_agent.guardrails.tools import (check_and_handle_unread_replace_lines, check_and_handle_drastic_shrinkage,
                                           check_and_handle_identical_write, check_and_handle_loop_guardrail)
from coding_agent.guardrails.self_verification import handle_self_verification_and_healing
from coding_agent.input_and_macro_handlers import (handle_user_input, handle_macros, handle_ast_extraction_interception,
                                                   handle_sandbox_guardrail)
from coding_agent.guardrail_and_verification_handlers import (handle_automated_follow_up, handle_empty_generation,
                                                              handle_unresolved_verification_alert, handle_unapplied_code_change,
                                                              validate_tool_call, handle_literal_ellipsis_anchor,
                                                              handle_content_recovery, handle_memory_regurgitation_on_read,
                                                              manage_tool_response)
from coding_agent.types import TurnStatus
from coding_agent import payload_parser

from cli import parse_cli_arguments
# the agent currently supports: Qwen2.5-Coder-7B-Instruct-Q4_K_M/Q5_K_M
from model_registry import MODEL_REGISTRY


class AgentState:
    def __init__(self, parsed_args):
        self.allow_patch = parsed_args["allow_patch"]
        self.force_testing = parsed_args["force_testing"]
        self.self_verify_py_writes = parsed_args["self_verify_py_writes"]
        self.kv_quantization_type = parsed_args["kv_quantization_type"]
        # toggle for per-guardrail hit tallying, printed once per user-turn cycle.
        # Falls back to False if the CLI arg parser doesn't define it yet.
        self.track_guardrail_hits = parsed_args.get("track_guardrail_hits", True)
        self.light_system_prompt = parsed_args["light_system_prompt"]

        self.active_config = MODEL_REGISTRY[parsed_args["model"]]

        self.target_path = Path(__file__).resolve().parent / "models" / self.active_config["filename"]
        self.loaded_model_name = self.active_config["display_name"]

        self.messages = []
        self.session_cwd = os.getcwd()

class AgentExecutionState:
    def __init__(self):
        self.is_split_mode: bool = False  # Indicates whether the agent is currently in split mode
        self.is_execute_mode: bool = False  # Indicates whether the agent is using AST interception
        self.original_split_file: str | None = None  # Stores the original file path when in split mode
        self.sandbox_directory: str | None = None  # Stores the path to the sandbox directory when in split mode
        self.automated_followup: str | None = None  # Buffer for system-generated prompt injections
        self.has_prompted_for_tests: bool = False  # Indicates whether the agent has prompted the user for tests

class AgentFlags:
    def __init__(self):
        self.file_was_modified = False  # Track if any files change during this cycle
        self.last_tool_call_signature = None  # Track the last tool run
        self.consecutive_errors = 0  # Track infinite loop traps
        self.consecutive_lint_failures = 0  # Track repeated self-verification failures on the same turn
        self.last_verification_failure = None  # Track {filepath, content, error} of the last failed lint, to detect stale-fix reuse
        self.recent_tool_signatures = []  # Loop Guardrail: track recent signatures, not just the immediately previous one
        self.awaiting_fix = False  # set when a run_cmd failure needs a follow-up fix
        self.last_run_cmd_error = None
        self.repair_required = False
        self.guardrail_hits = {}  # NEW: {guardrail_name: count} for this user-turn cycle
        self.tool_calls_this_turn = 0

    def record_hit(self, name, enabled=True):
        """No-op when tallying is disabled, so call sites never need an `if` wrapper."""
        if not enabled:
            return
        self.guardrail_hits[name] = self.guardrail_hits.get(name, 0) + 1

parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
state = AgentState(parsed_args)
execution_state = AgentExecutionState()


def main(state, execution_state):
    if state.light_system_prompt:
        system_prompt = system_prompt_builder_light.build_system_prompt(allow_patch=state.allow_patch)
    else:
        system_prompt = system_prompt_builder.build_system_prompt(allow_patch=state.allow_patch)

    initializer = LLMInitializer(state.target_path, state.loaded_model_name, state.active_config, state.kv_quantization_type)
    initializer.initialize_agent()
    context_window = initializer.CONTEXT_WINDOW
    llm = initializer.llm

    state.messages = [{"role": "system", "content": system_prompt}]

    display_welcome_banner(state.loaded_model_name, state.allow_patch)

    while True:
        # Check if we have an automated follow-up prompt queued
        if execution_state.automated_followup:
            user_input = execution_state.automated_followup
            execution_state.automated_followup = None
            print(f"\n[Automated User]: {user_input}")
        else:
            # smart input handler
            user_input = get_user_prompt()

        if handle_user_input(state, user_input, system_prompt):
            continue

        if handle_macros(user_input, state, execution_state, system_prompt, context_window):
            continue

        # Standard execution or continuation of sandbox mode
        state.messages.append({"role": "user", "content": user_input})

        agent_flags = AgentFlags()
        tool_args = {}  # Very important: this must exist in the first iteration

        # Internal Agent Execution Loop
        while True:
            check_context_guardrail(state.messages, llm, context_window)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages, context_window)
                if interrupted:
                    break

                if handle_empty_generation(response_content, agent_flags, state):
                    continue

                if execution_state.is_split_mode:
                    if handle_ast_extraction_interception(state, execution_state, response_content):
                        continue

                # Check for tool calls FIRST. Never let "Refactor Phase Complete"
                # short-circuit a turn that also contains a tool call.
                tool_request = payload_parser.extract_tool_call(response_content, allow_patch=state.allow_patch)

                if not tool_request:
                    # >>> Universal Completion Circuit Breaker <<<
                    is_completion_claim = "task complete" in response_content.lower()
                    last_user_msg = state.messages[-1].get("content", "") if state.messages else ""

                    # Check if the last interaction was a failure (e.g., failed patch_file or verification)
                    is_error_state = (
                            agent_flags.awaiting_fix or
                            ("System Alert:" in last_user_msg and "failed" in last_user_msg.lower()) or
                            "Error:" in last_user_msg
                    )

                    if is_completion_claim and is_error_state:
                        agent_flags.consecutive_errors += 1
                        if agent_flags.consecutive_errors >= 2:
                            print("🛑 [Circuit Breaker] Agent repeating 'Task Complete' after failures. Halting turn.")
                            agent_flags.consecutive_errors = 0
                            break  # Breaks the inner loop safely

                        agent_flags.record_hit("premature_completion_after_error", state.track_guardrail_hits)
                        state.messages.append({
                            "role": "user",
                            "content": (
                                "SYSTEM ERROR: You declared the task complete, but the previous action failed or unresolved errors remain. "
                                "You MUST issue a tool call (like `read_file`, `patch_file`, or `replace_lines`) to investigate and fix the issue. "
                                "Do NOT reply with 'Task Complete' until the fix is successfully applied."
                            )
                        })
                        continue
                    # >>> END FIX 1 <<<

                    turn_status = handle_memory_regurgitation_on_read(response_content, state, agent_flags)
                    if turn_status == TurnStatus.TRY_AGAIN:
                        continue
                    if turn_status == TurnStatus.END_TURN:
                        break

                    turn_status = handle_unresolved_verification_alert(state, agent_flags)
                    if turn_status == TurnStatus.TRY_AGAIN:
                        continue
                    if turn_status == TurnStatus.END_TURN:
                        break

                    turn_status = handle_unapplied_code_change(response_content, state, agent_flags)
                    if turn_status == TurnStatus.TRY_AGAIN:
                        continue
                    if turn_status == TurnStatus.END_TURN:
                        break

                    if execution_state.is_split_mode:
                        if handle_sandbox_guardrail(execution_state, state, response_content):
                            continue

                    if agent_flags.awaiting_fix:
                        agent_flags.record_hit("completion_blocked_pending_fix", state.track_guardrail_hits)
                        state.messages.append({
                            "role": "user",
                            "content": (
                                "System Alert: The verification command failed earlier in this turn. "
                                "The task is NOT complete. Do not claim completion yet. "
                                "You must make a real fix and run the verification command again. "
                                "Only a successful verification run can clear this state."
                            )
                        })
                        continue

                    handle_automated_follow_up(state, agent_flags, execution_state, tool_args)

                    break

                tool_name = tool_request.get("name")
                tool_args = tool_request.get("args", {})

                turn_status = validate_tool_call(tool_name, tool_args, agent_flags, state)
                if turn_status == TurnStatus.TRY_AGAIN:
                    continue
                if turn_status == TurnStatus.END_TURN:
                    break

                turn_status = handle_literal_ellipsis_anchor(tool_name, tool_args, agent_flags, state)
                if turn_status == TurnStatus.TRY_AGAIN:
                    continue
                if turn_status == TurnStatus.END_TURN:
                    break

                # --- PRE-FLIGHT VALIDATION & GUARDRAILS ---
                if tool_name in ["read_file", "run_cmd"] and "<payload>" in response_content:
                    agent_flags.record_hit("payload_on_wrong_tool", state.track_guardrail_hits)
                    state.messages.append({"role": "user",
                                     "content": f"System Alert: Tool `{tool_name}` does NOT accept <payload> blocks. Retry with ONLY the JSON block."})
                    continue

                if is_truncated and tool_name == "write_file":
                    raise json.JSONDecodeError("Incomplete payload due to context limit.", "", 0)

                # Path resolution
                for key in ["filepath", "dir_path"]:
                    if key in tool_args and not os.path.isabs(tool_args[key]):
                        tool_args[key] = os.path.abspath(os.path.join(state.session_cwd, tool_args[key]))

                # Define standard payload key handling
                content_key = "new_content" if tool_name == "patch_file" else "content"

                # Payload recovery, empty file guard & no-op / regurgitation guardrail
                if tool_name in ["write_file", "append_file", "patch_file", "replace_lines"]:
                    turn_status = handle_content_recovery(state, agent_flags, tool_args, content_key, tool_name)
                    if turn_status == TurnStatus.END_TURN:
                        break
                    elif turn_status == TurnStatus.TRY_AGAIN:
                        continue

                    if tool_name == "write_file":
                        intercepted, should_break = check_and_handle_identical_write(tool_args, state, agent_flags,
                                                                                     content_key)
                        if intercepted:
                            if should_break:
                                break
                            continue
                        if check_and_handle_drastic_shrinkage(tool_args, state, agent_flags, content_key):
                            continue

                if tool_name == "write_file" and agent_flags.repair_required and os.path.isfile(tool_args.get("filepath", "")):
                    agent_flags.record_hit("repair_required_write_block", state.track_guardrail_hits)
                    state.messages.append({
                        "role": "user",
                        "content": (
                            "System Alert: `write_file` is blocked during repair of an existing file.\n"
                            "The previous change failed verification.\n"
                            "You must preserve the existing file and make a targeted fix using "
                            "`patch_file` or `replace_lines`.\n"
                            "Read the relevant code first if necessary. Do not regenerate the "
                            "entire file."
                        )
                    })
                    continue

                if check_and_handle_unread_replace_lines(tool_name, tool_args, state, agent_flags):
                    agent_flags.record_hit("stale_read_guard", state.track_guardrail_hits)
                    continue

                intercepted, should_break = check_and_handle_loop_guardrail(tool_name, tool_args, state, agent_flags)
                if intercepted:
                    if should_break:
                        break
                    continue

                # --- EXECUTION ---
                print(f"\n⚠️  AGENT REQUESTS EXECUTION: {tool_name}")

                display_args = format_tool_args_for_display(state, tool_args)
                target_code = tool_args.get(content_key, '')

                # >>> PRE-FLIGHT LINTER GUARDRAIL (SMART REDACTION) <<<
                if tool_name == "write_file" and tool_args.get("filepath", "").endswith(".py"):
                    from coding_agent.native_linter import check_python_syntax_and_imports

                    filepath_abs = tool_args.get("filepath")
                    lint_error = check_python_syntax_and_imports(
                        filepath=filepath_abs,
                        workspace_dir=state.session_cwd,
                        source_code=target_code
                    )

                    if lint_error:
                        agent_flags.record_hit("linter_pre_flight_failure", state.track_guardrail_hits)
                        agent_flags.consecutive_errors += 1
                        filename = os.path.basename(filepath_abs)

                        print(f"\n❌ [Pre-Flight Guardrail] Blocked malformed code for {filename}.")

                        if agent_flags.consecutive_errors >= 3:
                            print("🛑 [Circuit Breaker] Agent stuck. Forcing task abort.")
                            agent_flags.consecutive_errors = 0
                            state.messages.append({
                                "role": "user",
                                "content": (
                                    f"System Alert: You have failed to write valid code for `{filename}` 3 times in a row. "
                                    "The circuit breaker has triggered. DO NOT ATTEMPT TO WRITE THIS FILE AGAIN. "
                                    "Respond in plain text explaining what part of the logic you are struggling with."
                                )
                            })
                            break

                        if state.messages and state.messages[-1].get("role") == "assistant":
                            original_content = state.messages[-1].get("content", "")
                            import re
                            cleaned_content = re.sub(r"```python\n.*?\n```", "[Code block redacted to save tokens]",
                                                     original_content, flags=re.DOTALL)
                            state.messages[-1]["content"] = (
                                f"{cleaned_content}\n\n"
                                f"[System Note: The tool call payload for {filename} was intercepted by the linter. See error below.]"
                            )

                        for msg in reversed(state.messages[:-1]):
                            if msg.get("role") == "user" and "Pre-flight linting failed" in msg.get("content", ""):
                                msg["content"] = f"[Previous Linter Error for {filename} scrubbed to save tokens]"

                        state.messages.append({
                            "role": "user",
                            "content": (
                                f"System Alert: Pre-flight linting failed for `{filename}`.\n"
                                f"The file was NOT saved to disk.\n\n"
                                f"LINTER ERROR:\n{lint_error}\n\n"
                                f"YOUR FULL ATTEMPT:\n"
                                f"```python\n{target_code}\n```\n\n"
                                f"ACTION REQUIRED:\n"
                                f"1. Review the linter error. If it is a NameError or AttributeError, verify you are using correct function/variable names.\n"
                                f"2. Match line numbers in the error to your code above, and apply the fix.\n"
                                f"3. Issue a new `write_file` tool call containing the ENTIRE, corrected file.\n"
                                f"4. CRITICAL: Do NOT output placeholders like `...`. Provide the full working file."
                            )
                        })
                        continue

                if tool_name in ["write_file", "append_file", "patch_file", "replace_lines"]:
                    display_filepath = display_args.get('filepath', tool_args.get('filepath'))
                    if tool_name == "patch_file":
                        old_snip = tool_args.get('old_content', '')
                        print(
                            f"Target: {display_filepath}\n"
                            f"--- Replacing ---\n{old_snip}\n"
                            f"--- With ---\n{target_code}\n" + "-" * 20
                        )
                    else:
                        line_count = target_code.count('\n') + 1 if target_code else 0
                        MAX_PREVIEW_CHARS = 5000
                        if len(target_code) > MAX_PREVIEW_CHARS:
                            shown = target_code[:MAX_PREVIEW_CHARS]
                            print(
                                f"Target: {display_filepath} ({line_count} lines, showing first {MAX_PREVIEW_CHARS} chars)\n" + "-" * 20 + f"\n{shown}\n...\n" + "-" * 20
                            )
                        else:
                            print(
                                f"Target: {display_filepath} ({line_count} lines)\n" + "-" * 20 + f"\n{target_code}\n" + "-" * 20
                            )
                else:
                    print(f"Arguments: {display_args}")

                approval = input("Allow this action? (y/n/edit): ").strip().lower()
                tool_result, tool_reinforcement = "", ""

                if approval == 'y':
                    tool_result, tool_reinforcement, was_mod = execute_tool(tool_name, tool_args, execution_state.is_split_mode)
                    agent_flags.tool_calls_this_turn += 1
                    agent_flags.file_was_modified = agent_flags.file_was_modified or was_mod
                    # Reset awaiting fix flag
                    if tool_name in ["write_file", "append_file", "patch_file", "replace_lines"] and was_mod:
                        agent_flags.awaiting_fix = False
                    print(f"⚙️  Tool execution finished.")

                    # Auxiliary output of the tool_result, useful for debugging
                    MAX_RESULT_PREVIEW = 400
                    # MAX_RESULT_PREVIEW = 2000
                    if len(tool_result) > MAX_RESULT_PREVIEW:
                        preview = tool_result[:MAX_RESULT_PREVIEW]
                        print(f"   Result ({len(tool_result)} chars, truncated): {preview}...")
                    else:
                        print(f"   Result: {tool_result}")

                    # --- catch patch_file execution-time failures before they're silently ignored ---
                    if tool_name == "patch_file" and "Error" in tool_result:
                        agent_flags.record_hit("patch_file_execution_failure", state.track_guardrail_hits)
                        tool_reinforcement += (
                            f"\n\nSystem Alert: `patch_file` failed — the file was NOT changed. The task is not "
                            f"complete. Review the error above and retry with a corrected old_content, or switch "
                            f"to replace_lines."
                        )

                    # Self-Verification
                    success, tool_reinforcement = handle_self_verification_and_healing(state, tool_name, tool_args,
                                                                                       agent_flags, tool_reinforcement, was_mod,
                                                                                       tool_result)
                    if not success:
                        state.messages.append(
                            {"role": "user", "content": f"Tool Result:\n{tool_result}{tool_reinforcement}"})
                        break

                    tool_reinforcement += f"\n\nSystem Alert: Tool executed successfully."

                    tool_reinforcement = manage_tool_response(tool_name, tool_args, tool_result, agent_flags, state, tool_reinforcement)

                elif approval == 'edit':
                    tool_result = f"User denied and provided feedback: {input('Feedback: ')}"
                else:
                    tool_result = "User denied permission."
                    print("🛑 Action blocked.")

                state.messages.append(
                    {"role": "user", "content": f"Tool Execution Result:\n{tool_result}{tool_reinforcement}"})

            # potential recovery from misformatted JSONs
            except json.JSONDecodeError as e:
                agent_flags.record_hit("json_decode_error", state.track_guardrail_hits)
                print(f"\n❌ [Parser Interceptor] Caught JSON error, feeding back to agent...")
                error_msg = (
                    f"⚠️ ACTION FAILED: Your tool call was NOT executed because the JSON is invalid.\n"
                    f"JSON Error: {str(e)}\n\n"
                    f"Hint: In JSON, you cannot escape single quotes like \\'. To put a literal backslash "
                    f"and a quote in Python code via JSON, you must double-escape the backslash: \\\\', "
                    f"or avoid illegal JSON escape sequences. Please fix your JSON and try again."
                )
                # more directive retry message
                error_msg += (
                    "\n\nDo NOT explain this error in prose. Do NOT tell the user to run any command "
                    "manually — you have a `run_cmd` tool for that and must use it yourself. "
                    "Immediately retry with a single corrected <tool_call>{...}</tool_call> block "
                    "and nothing else."
                )
                state.messages.append({"role": "user", "content": error_msg})

                agent_flags.consecutive_errors += 1
                if agent_flags.consecutive_errors >= 3:
                    print("🛑 [Circuit Breaker] Agent stuck in JSON formatting loop. Forcing exit.")
                    break

                continue  # CRITICAL: 'continue' lets the agent retry instantly

            except Exception as e:
                print(f"\n[Error during generation]: {e}")
                break

            finally:
                render_token_footer(state.messages, llm, context_window)

        # --- Print the per-turn guardrail tally, if tracking is enabled and anything fired ---
        if state.track_guardrail_hits and agent_flags.guardrail_hits:
            total = sum(agent_flags.guardrail_hits.values())
            print(f"📊 [Guardrail Tally] total={total} | {agent_flags.guardrail_hits}")


if __name__ == "__main__":
    main(state, execution_state)
