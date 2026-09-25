import sys
import os
import json
import re
import shutil
from enum import Enum

from pathlib import Path

from common.llm_init import LLMInitializer
from common.input_handler import get_user_prompt
from common.tool_definitions import read_file
from common.execute_tool import execute_tool
from common.guardrail_tools import check_context_guardrail
from common.output_handler import stream_agent_response

from coding_agent.welcome_banner import display_welcome_banner
from coding_agent import system_prompt_builder, system_prompt_builder_restrictive
from coding_agent.native_helpers import (get_repo_structure, generate_requirements_native, gather_deep_context,
                                         gather_deep_context_ast)
from coding_agent.guardrails.tools import (verify_sandbox_health, find_last_code_block,
                                           check_and_handle_unread_replace_lines,
                                           check_and_handle_drastic_shrinkage,
                                           looks_like_unapplied_code_change, check_and_handle_identical_write,
                                           check_and_handle_loop_guardrail, looks_like_memory_regurgitation_on_read_request)
from coding_agent.guardrails.definitions import READ_ONLY_TOOLS, TOOL_FORBIDDEN_FIELDS, TOOL_REQUIRED_FIELDS
from coding_agent.guardrails.self_verification import handle_self_verification_and_healing
from coding_agent import hidden_readme_prompt_builder
from coding_agent import split_tools
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
        # NEW: toggle for per-guardrail hit tallying, printed once per user-turn cycle.
        # Falls back to False if the CLI arg parser doesn't define it yet.
        self.track_guardrail_hits = parsed_args.get("track_guardrail_hits", True)
        self.restrictive = parsed_args["restrictive"]

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

parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
state = AgentState(parsed_args)
execution_state = AgentExecutionState()


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


def handle_user_input(state, user_input, system_prompt):
    if user_input == "/quit":
        print("Exiting. Goodbye!")
        sys.exit(0)

    if user_input == "/clear":
        state.messages = [{"role": "system", "content": system_prompt}]
        state.session_cwd = os.getcwd()
        state.consult_read_cache = {}
        print("🧹 Memory and environment completely cleared!")
        return True

    if user_input == "/cancel":
        print("❌ Current draft discarded.")
        return True

    if not user_input:
        return True

    return False


def handle_macros(user_input, state, execution_state, system_prompt):
    """
    Handles the more complex macros. Returns True if a relevant macro was found but an error occurred
    and False if everything went right or no relevant macro was found.
    """
    # --- MACRO: /requirements ---
    if user_input.startswith("/requirements"):
        no_version_flag = "--no-version" in user_input
        cleaned_input = user_input.replace("--no-version", "").strip()
        parts = cleaned_input.split(" ", 1)
        target_dir = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "."

        abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
        state.session_cwd = abs_target_dir

        if not os.path.isdir(abs_target_dir):
            print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
            return True

        print(f"\n⚠️  MANUAL OVERRIDE: Generate requirements.txt natively? (No versions: {no_version_flag})")
        approval = input("Allow this action? (y/n): ").strip().lower()

        if approval == 'y':
            tool_result = generate_requirements_native(abs_target_dir, no_version=no_version_flag)
            state.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",
                 "content": f"System Alert: User manually ran /requirements for '{abs_target_dir}'. Result: {tool_result}. Briefly acknowledge completion."}
            ]
        else:
            print("🛑 Action blocked.")
            return True

        return False

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
        state.session_cwd = abs_target_dir

        if not os.path.isdir(abs_target_dir):
            print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
            return True

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
            readme_path, state.allow_patch,
            deep_focus=(deep_focus or deep_ast_focus)
        )

        hidden_readme_prompt = hidden_readme_prompt_builder.build_hidden_readme_prompt(
            abs_target_dir, repo_tree, existing_readme, strategy_steps, code_summary=code_summary,
            cli_help=cli_help
        )

        state.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": hidden_readme_prompt}
        ]

        return False

    # --- MACRO: /split ---
    elif user_input.startswith("/split"):
        execute_mode = "--execute" in user_input
        cleaned_args = user_input.replace("--execute", "").strip().split(" ", 1)

        if len(cleaned_args) < 2 or not cleaned_args[1].strip():
            print("❌ Error: You must provide a filepath. Usage: /split [--execute] [filepath]")
            return True

        target_file = cleaned_args[1].strip()
        abs_target_file = os.path.abspath(os.path.expanduser(target_file))

        if not os.path.isfile(abs_target_file):
            print(f"❌ Error: Target file '{abs_target_file}' does not exist.")
            return True

        if execute_mode:
            print(
                f"\n⚠️  [WARNING] Execution Mode Active: System will use AST natively based on LLM JSON mapping.")
            print("   This isolates the agent from hallucinating logic blocks.")
            print(f"🔍 Initializing Sandbox and Parsing AST structure for {abs_target_file}...")
        else:
            print(f"\n🔍 Initializing Sandbox (Advisor Mode) for {abs_target_file}...")

        # 1. Setup sandbox tracking
        _, execution_state.sandbox_directory = split_tools.setup_refactor_sandbox(abs_target_file)
        execution_state.original_split_file = abs_target_file
        execution_state.is_split_mode = True
        execution_state.is_execute_mode = execute_mode

        # 2. Divert agent's current working directory to the sandbox!
        state.session_cwd = execution_state.sandbox_directory

        # Pass the flag to the prompt builder
        split_prompt = split_tools.build_split_prompt(abs_target_file, state.session_cwd, execute_mode=execute_mode)

        if not execute_mode:
            split_prompt += "\n\nFormat your plan now. Do not write file contents yet. Wait for confirmation."

        state.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": split_prompt}
        ]

        return False

    return False  # Return False if no macro was handled


def handle_ast_extraction_interception(state, execution_state, response_content):
    if "```json" in response_content:
        if execution_state.is_execute_mode:
            handled, alert = split_tools.handle_ast_extraction(response_content,
                                                               execution_state.original_split_file,
                                                               execution_state.sandbox_directory)
        else:
            print("\n📋 Advisor blueprint received:")
            approval = input(
                "Apply this blueprint deterministically to the sandbox? (y/n): ").strip().lower()
            if approval == 'y':
                handled, alert = split_tools.handle_ast_extraction(response_content,
                                                                   execution_state.original_split_file,
                                                                   execution_state.sandbox_directory)
            else:
                handled, alert = True, (
                    "Blueprint held pending revision. If you'd like changes, "
                    "explain them and provide an updated ```json blueprint."
                )
        if handled:
            state.messages.append({"role": "user", "content": alert})
            return True
    return False


def handle_sandbox_guardrail(execution_state, state, response_content):
    """SANDBOX GUARDRAIL: Only runs if NO tool was called."""
    if any(x in response_content.lower() for x in ["refactor phase complete", "task complete"]):
        passed, report = verify_sandbox_health(execution_state.original_split_file,
                                               execution_state.sandbox_directory, state.messages)
        if passed:
            print(f"✅ Sandbox passed! Staged in: {execution_state.sandbox_directory}")
            if execution_state.is_execute_mode:
                # Promotion done only in execute mode, just for safety
                if input("Promote to production? (y/n): ").strip().lower() == 'y':
                    target_dir = os.path.dirname(execution_state.original_split_file)
                    for item in os.listdir(execution_state.sandbox_directory):
                        if not item.startswith('.'):
                            shutil.copy2(os.path.join(execution_state.sandbox_directory, item),
                                         os.path.join(target_dir, item))
                    print("🚀 Files successfully promoted.")
            execution_state.is_split_mode = execution_state.is_execute_mode = False
            state.session_cwd = os.path.dirname(execution_state.original_split_file)
            return True
        print(f"❌ Verification Failed:\n{report}")
        state.messages.append({"role": "user",
                               "content": f"System Verification Failed:\n{report}\n\nCorrect this error and output 'Refactor Phase Complete'."})
        return False
    return False


def handle_automated_follow_up(state, agent_flags, execution_state, tool_args):
    """Automated follow-up trigger"""
    if state.force_testing and agent_flags.file_was_modified and not execution_state.is_split_mode:
        # Directly use .get() since tool_args is guaranteed to be a dict
        raw_path = tool_args.get("filepath", "")
        fn = Path(raw_path).name.lower()
        if (raw_path and fn.endswith(".py") and
                (fn.startswith("test_") or fn.endswith("_test.py")) and not execution_state.has_prompted_for_tests):
            print("\n[System]: Automatically queuing follow-up test prompt.")
            safe_exec = sys.executable.replace("\\", "/")
            state.messages.append({"role": "user",
                                   "content": f"Great. Use `run_cmd` (e.g. `\"{safe_exec}\" -m unittest`) to verify. If a test fails, analyze if the test itself is wrong before fixing the source code."})
            execution_state.has_prompted_for_tests = True
        else:
            print("\n[System]: Main script written / modified.")


class TurnStatus(Enum):
    OK = 1
    END_TURN = 2
    TRY_AGAIN = 3


def handle_empty_generation(response_content, agent_flags, state):
    """Catch empty/near-empty generations before anything else processes them."""
    if not response_content or len(response_content.strip()) < 5:
        agent_flags.consecutive_errors += 1
        agent_flags.record_hit("empty_generation", state.track_guardrail_hits)
        if agent_flags.consecutive_errors >= 3:
            print("🛑 [Circuit Breaker] Model producing empty responses. Forcing turn end.")
            return True
        print("🛡️  [Guardrail] Empty or near-empty generation detected — prompting to continue.")
        state.messages.append({
            "role": "user",
            "content": (
                "System Alert: your last response was empty. If the previous action succeeded, "
                "state that briefly. If more work remains, take the next action now."
            )
        })
        return True
    return False


def handle_unresolved_verification_alert(state, agent_flags):
    if getattr(agent_flags, "last_action_was_unresolved_alert", False):
        agent_flags.consecutive_errors += 1
        agent_flags.record_hit("declared_complete_with_unresolved_alert", state.track_guardrail_hits)
        if agent_flags.consecutive_errors >= 3:
            print(
                "🛑 [Circuit Breaker] Agent repeatedly ignores unresolved verification failures. Forcing turn end.")
            return TurnStatus.END_TURN
        print(
            "🛡️  [Guardrail] Response ended without a tool call, but the last verification failure is still unresolved.")
        state.messages.append({"role": "user", "content":
            "System Alert: the last verification failure has NOT been resolved. You must call a "
            "tool now to actually fix it — do not end the turn or say the task is complete."})
        return TurnStatus.TRY_AGAIN
    return TurnStatus.OK


def handle_unapplied_code_change(response_content, state, agent_flags):
    last_user_msg = next(
        (m["content"] for m in reversed(state.messages) if m.get("role") == "user"),
        ""
    )
    if looks_like_unapplied_code_change(response_content, last_user_message=last_user_msg):
        agent_flags.consecutive_errors += 1
        agent_flags.record_hit("unapplied_code_change", state.track_guardrail_hits)
        if agent_flags.consecutive_errors >= 3:
            print(
                "🛑 [Circuit Breaker] Agent repeatedly shows code without applying it. Forcing turn end.")
            return TurnStatus.END_TURN
        print(
            "🛡️  [Guardrail] Response contained a code block but no tool call — prompting to apply it.")
        state.messages.append({"role": "user", "content":
            "System Alert: your last response showed a code change but did not call a tool. "
            "Nothing has changed on disk and the task is not complete. Call the appropriate "
            "tool now (e.g. write_file, replace_lines, patch_file) to actually apply the change "
            "you just described."})
        return TurnStatus.TRY_AGAIN
    return TurnStatus.OK


def validate_tool_call(tool_name, tool_args, agent_flags, state) -> TurnStatus:
    """SCHEMA VALIDATION: catch malformed calls the parser extracted but didn't validate."""
    required = TOOL_REQUIRED_FIELDS.get(tool_name, set())
    missing = required - tool_args.keys()
    forbidden_present = TOOL_FORBIDDEN_FIELDS.get(tool_name, set()) & tool_args.keys()

    if missing or forbidden_present:
        parts = []
        if forbidden_present:
            other_tool = next(
                (t for t, fields in TOOL_REQUIRED_FIELDS.items()
                 if forbidden_present & fields and t != tool_name),
                None
            )
            parts.append(
                f"field(s) {sorted(forbidden_present)} belong to `{other_tool}`, not `{tool_name}`"
                if other_tool else f"unexpected field(s) {sorted(forbidden_present)} for `{tool_name}`"
            )
        if missing:
            parts.append(f"missing required field(s) {sorted(missing)} for `{tool_name}`")

        state.messages.append({"role": "user", "content":
            f"System Alert: `{tool_name}` call rejected — {'; '.join(parts)}. "
            f"Check the tool's exact argument names and retry with the correct shape."})

        agent_flags.record_hit("schema_validation", state.track_guardrail_hits)
        agent_flags.consecutive_errors += 1
        if agent_flags.consecutive_errors >= 3:
            print("🛑 [Circuit Breaker] Agent stuck sending malformed tool calls. Forcing turn end.")
            return TurnStatus.END_TURN
        return TurnStatus.TRY_AGAIN
    return TurnStatus.OK


def handle_literal_ellipsis_anchor(tool_name, tool_args, agent_flags, state):
    """Catch literal '...' placeholders copied verbatim into replace_lines anchors."""
    if tool_name == "replace_lines" and "..." in tool_args.get("expected_start_snippet", ""):
        agent_flags.record_hit("literal_ellipsis_anchor", state.track_guardrail_hits)

        actual_line = None
        fp_raw = tool_args.get("filepath", "")
        fp_abs = fp_raw if os.path.isabs(fp_raw) else os.path.abspath(
            os.path.join(state.session_cwd, fp_raw))
        start_line = tool_args.get("start_line")
        if os.path.isfile(fp_abs) and start_line:
            with open(fp_abs, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if 0 < start_line <= len(lines):
                actual_line = lines[start_line - 1].rstrip("\n")

        hint = f" The actual line is: {actual_line!r}" if actual_line else ""
        state.messages.append({"role": "user", "content":
            f"System Alert: `expected_start_snippet` contains a literal '...' — this looks like you copied "
            f"an illustrative placeholder instead of the real line content.{hint} Retry with the exact text."})

        agent_flags.consecutive_errors += 1
        if agent_flags.consecutive_errors >= 3:
            print("🛑 [Circuit Breaker] Agent stuck submitting placeholder anchors. Forcing turn end.")
            return TurnStatus.END_TURN
        return TurnStatus.TRY_AGAIN
    return TurnStatus.OK


def handle_content_recovery(state, agent_flags, tool_args, content_key, tool_name):
    content_clean = re.sub(r'```[a-zA-Z]*\s*```', '', tool_args.get(content_key, '')).strip()

    if not content_clean:
        recovered = find_last_code_block(state.messages)
        is_stale = bool(recovered and agent_flags.last_verification_failure and
                        agent_flags.last_verification_failure.get("filepath") == tool_args.get("filepath") and
                        recovered.strip() == agent_flags.last_verification_failure.get("content", "").strip())

        if recovered and recovered.strip() and not is_stale:
            agent_flags.record_hit("content_recovery", state.track_guardrail_hits)
            print(f"🔧 [Recovery] Reusing last drafted code block for {tool_name}.")
            tool_args[content_key] = recovered
        else:
            agent_flags.record_hit(
                "stale_fix_guard" if is_stale else "empty_content_block",
                state.track_guardrail_hits
            )
            msg = (f"System Alert: Blocked empty {tool_name}." if not is_stale else
                   f"System Alert: Stale-Fix Guard. You provided the SAME failing code again.\n{agent_flags.last_verification_failure.get('error', '')}")
            msg += f"\nYou MUST provide the corrected code inside a <payload> block. Retry {tool_name}."

            agent_flags.consecutive_errors += 1
            if agent_flags.consecutive_errors >= 3:
                print("🛑 [Circuit Breaker] Agent stuck in syntax loop. Forcing exit.")
                return TurnStatus.END_TURN
            state.messages.append({"role": "user", "content": msg})
            return TurnStatus.TRY_AGAIN
        return TurnStatus.OK


def handle_memory_regurgitation_on_read(response_content, state, agent_flags):
    last_user_msg = next(
        (m["content"] for m in reversed(state.messages) if m.get("role") == "user"),
        ""
    )
    if looks_like_memory_regurgitation_on_read_request(
        response_content, last_user_msg, agent_flags.tool_calls_this_turn
    ):
        agent_flags.consecutive_errors += 1
        agent_flags.record_hit("memory_regurgitation_on_read", state.track_guardrail_hits)
        if agent_flags.consecutive_errors >= 3:
            print("🛑 [Circuit Breaker] Agent repeatedly recites from memory instead of reading. Forcing turn end.")
            return TurnStatus.END_TURN
        print("🛡️  [Guardrail] User asked to read a file; response contains code but no read_file/read_symbol call.")
        state.messages.append({"role": "user", "content":
            "System Alert: the user asked you to READ an existing file. You must NOT write code "
            "from memory or invent an implementation — you have not actually seen this file's "
            "contents yet. Call read_file now to see the real content before saying anything about it."})
        return TurnStatus.TRY_AGAIN
    return TurnStatus.OK


def manage_tool_response(tool_name, tool_result, agent_flags, state, tool_reinforcement):
    FAILURE_SIGNALS = ("Traceback", "Error", "FAILED", "SyntaxError", "Exception")

    if tool_name in READ_ONLY_TOOLS:
        if agent_flags.awaiting_fix:
            agent_flags.record_hit("fix_context_reminder", state.track_guardrail_hits)
            FIX_CONTEXT_REMINDER = (
                "\n\n[System note: you are still resolving the command failure from earlier in this "
                "turn. If this read confirmed the cause, apply the fix now with a real "
                "write_file/patch_file/replace_lines call — target whichever file actually has the "
                "bug (the source file or a test file you wrote). Then re-run the command to verify.]"
            )
            tool_reinforcement += FIX_CONTEXT_REMINDER
        else:
            # --- IMPORTANT GUARDRAIL: inject reminder right after read-only tool results ---
            agent_flags.record_hit("inspect_reminder", state.track_guardrail_hits)
            INSPECT_REMINDER = (
                "\n\n[System note: the above was a read-only inspection result. Only call "
                "write_file, append_file, patch_file, or replace_lines if the user explicitly "
                "asked for a code change in their most recent message. Otherwise, respond now "
                "in plain text summarizing what you found — do not emit a tool call.]"
            )
            tool_reinforcement += INSPECT_REMINDER
    elif tool_name == "run_cmd" and any(sig in tool_result for sig in FAILURE_SIGNALS):
        # A nudge for immediate fix if tests are failing
        agent_flags.record_hit("run_cmd_failure_reminder", state.track_guardrail_hits)
        tool_reinforcement += (
            "\n\n[System note: the command failed. If you know the fix, apply it now with a "
            "real write_file/patch_file/replace_lines tool call — do not describe the fix in "
            "a markdown code block. Do not call run_cmd again until the fix has actually been "
            "applied via a tool call.]"
        )
        agent_flags.awaiting_fix = True
        agent_flags.last_run_cmd_error = tool_result
    return tool_reinforcement


def main(state, execution_state):
    if state.restrictive:
        system_prompt = system_prompt_builder_restrictive.build_system_prompt(allow_patch=state.allow_patch)
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

        if handle_macros(user_input, state, execution_state, system_prompt):
            continue

        # Standard execution or continuation of sandbox mode
        state.messages.append({"role": "user", "content": user_input})

        agent_flags = AgentFlags()
        tool_args = {}  # Very important: this must exist in the first iteration

        # Internal Agent Execution Loop
        while True:
            check_context_guardrail(state.messages, llm, context_window)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages)
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

                    tool_reinforcement = manage_tool_response(tool_name, tool_result, agent_flags, state, tool_reinforcement)

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

        # --- Print the per-turn guardrail tally, if tracking is enabled and anything fired ---
        if state.track_guardrail_hits and agent_flags.guardrail_hits:
            total = sum(agent_flags.guardrail_hits.values())
            print(f"📊 [Guardrail Tally] total={total} | {agent_flags.guardrail_hits}")


if __name__ == "__main__":
    main(state, execution_state)
