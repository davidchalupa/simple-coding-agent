import sys
import os
import re

from pathlib import Path

from coding_agent.guardrails.tools import (find_last_code_block, looks_like_unapplied_code_change,
                                           looks_like_memory_regurgitation_on_read_request)
from coding_agent.guardrails.definitions import READ_ONLY_TOOLS, TOOL_FORBIDDEN_FIELDS, TOOL_REQUIRED_FIELDS
from coding_agent.types import TurnStatus


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


def manage_tool_response(tool_name, tool_args, tool_result, agent_flags, state, tool_reinforcement):
    FAILURE_SIGNALS = ("Traceback", "Error", "FAILED", "SyntaxError", "Exception", "FAIL:")

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
            agent_flags.record_hit("inspect_reminder", state.track_guardrail_hits)
            INSPECT_REMINDER = (
                "\n\n[System note: the above was a read-only inspection result. Only call "
                "write_file, append_file, patch_file, or replace_lines if the user explicitly "
                "asked for a code change in their most recent message. Otherwise, respond now "
                "in plain text summarizing what you found — do not emit a tool call.]"
            )
            tool_reinforcement += INSPECT_REMINDER

    elif tool_name == "run_cmd" and any(sig in str(tool_result) for sig in FAILURE_SIGNALS):
        agent_flags.record_hit("run_cmd_failure_reminder", state.track_guardrail_hits)

        command_run = tool_args.get("command", "").lower()
        test_keywords = ["pytest", "unittest", "test.py", "test_"]
        is_test_run = any(kw in command_run for kw in test_keywords)

        if is_test_run:
            tool_reinforcement += (
                "\n\n[System note: The test suite failed. Because you likely just wrote or modified "
                "this test, the test setup itself is often flawed. Consider these common testing principles:\n"
                "1. Global Scope: Local variables declared in a test function do not override module-level "
                "globals in imported files. Override imported module attributes directly (e.g., "
                "`import target_module; target_module.CONFIG_VAR = test_val`).\n"
                "2. Determinism: Avoid relying on randomized data generators or dynamic setup functions. "
                "Hardcode explicit, minimal static fixtures/states so test execution is 100% predictable.\n"
                "3. State Isolation: Ensure mock objects or test state mutations are completely reset "
                "between test cases to prevent cascading failures.\n\n"
                "Do NOT assume the production code is broken. Fix the test file first before modifying production code."
            )
        else:
            tool_reinforcement += (
                "\n\n[System note: The command failed. If you know the fix, apply it now to the source code."
            )

        # Enforce direct tool execution regardless of failure type
        tool_reinforcement += (
            " Apply the fix with a real write_file/patch_file/replace_lines tool call — do not describe "
            "the fix in a markdown code block. Do not call run_cmd again until the fix has actually been "
            "applied via a tool call.]"
        )

        agent_flags.awaiting_fix = True
        agent_flags.last_run_cmd_error = tool_result

    return tool_reinforcement
