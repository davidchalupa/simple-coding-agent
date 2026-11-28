import sys
import os
import re

from pathlib import Path

from coding_agent.guardrails.tools import (find_last_code_block, looks_like_unapplied_code_change,
                                           _normalise_verification_path)
from coding_agent.guardrails.definitions import READ_ONLY_TOOLS, TOOL_FORBIDDEN_FIELDS, TOOL_REQUIRED_FIELDS
from coding_agent.guardrails.symbol_integrity import detect_partial_symbol_redefinition
from coding_agent.guardrails.execution_guardrails.read_guards import looks_like_memory_regurgitation_on_read_request
from coding_agent.types import TurnStatus

def record_recovery_progress(tool_name, tool_result, was_mod, agent_flags) -> bool:
    """Reset the error streak only after a required recovery succeeds."""
    pending = getattr(agent_flags, "recovery_pending", None)
    if not pending:
        return False

    result_text = str(tool_result).lstrip().lower()
    if result_text.startswith(("error:", "❌", "⚠️ action failed", "traceback")):
        return False

    successful_read = (
        pending == "fresh_read"
        and tool_name in {"read_file", "read_symbol"}
    )
    successful_edit = (
        pending in {"apply_change", "complete_symbol_edit"}
        and tool_name in {"write_file", "append_file", "patch_file", "replace_lines"}
        and bool(was_mod)
    )
    if not (successful_read or successful_edit):
        return False

    agent_flags.consecutive_errors = 0
    agent_flags.recovery_pending = None

    return True


def mark_post_edit_verification_required(
    tool_name, tool_args, was_mod, agent_flags, state
):
    """Require read-back after a successful Python source edit."""
    modifying_tools = {
        "write_file", "append_file", "patch_file", "replace_lines"
    }

    if tool_name not in modifying_tools or not was_mod:
        return

    filepath = _normalise_verification_path(
        tool_args.get("filepath"), state
    )

    if filepath and Path(filepath).suffix.lower() in {".py", ".pyi"}:
        agent_flags.post_edit_verification_pending.add(filepath)


def record_post_edit_readback(
    tool_name, tool_args, tool_result, agent_flags, state
):
    """Clear a pending requirement only after reading back that same file."""
    if tool_name not in {"read_file", "read_symbol"}:
        return None

    filepath = _normalise_verification_path(
        tool_args.get("filepath"), state
    )
    if not filepath:
        return None

    pending = agent_flags.post_edit_verification_pending
    if filepath not in pending:
        return None

    result_text = str(tool_result).lstrip().lower()

    # Match the failure conventions already used by record_recovery_progress.
    if not result_text or result_text.startswith((
        "error:", "❌", "⚠️ action failed", "traceback",
        "file not found", "symbol not found"
    )):
        return None

    pending.remove(filepath)
    return filepath


def handle_pending_post_edit_readback(state, agent_flags) -> TurnStatus:
    """Prevent the agent from ending a turn before reading back its edits."""
    pending = getattr(agent_flags, "post_edit_verification_pending", set())

    if not pending:
        return TurnStatus.OK

    paths = "\n".join(f"- {path}" for path in sorted(pending))

    agent_flags.record_hit(
        "post_edit_readback_required",
        state.track_guardrail_hits
    )

    state.messages.append({
        "role": "user",
        "content": (
            "SYSTEM ALERT: POST-EDIT READ-BACK REQUIRED\n\n"
            "You successfully modified Python source code, but have not yet "
            "read back the saved version. The task is NOT ready for completion.\n\n"
            f"File(s) requiring read-back:\n{paths}\n\n"
            "REQUIRED ACTION:\n"
            "1. Call read_symbol for the modified function or class, where possible. "
            "Otherwise use read_file to inspect the changed code.\n"
            "2. Inspect the actual saved contents, not the replacement payload "
            "you intended to write.\n"
            "3. Compare the saved implementation against the user's original "
            "requirements.\n"
            "4. If anything is missing or incorrect, fix it and read it back again.\n\n"
            "Do not declare Task Complete until the read-back has been inspected."
        )
    })

    return TurnStatus.TRY_AGAIN


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
        agent_flags.recovery_pending = "apply_change"
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


def handle_partial_symbol_replacement(tool_name, tool_args, agent_flags, state) -> TurnStatus:
    """Block replace_lines calls that redefine only part of an existing symbol.

    This specifically prevents a replacement from starting inside a function or
    class, leaving its original header/docstring in place, and inserting a second
    same-named definition that becomes nested inside the original symbol.
    """
    if tool_name != "replace_lines":
        return TurnStatus.OK

    filepath = tool_args.get("filepath", "")
    if not isinstance(filepath, str) or Path(filepath).suffix.lower() not in {".py", ".pyi"}:
        return TurnStatus.OK

    start_line = tool_args.get("start_line")
    end_line = tool_args.get("end_line")
    replacement_content = tool_args.get("content", "")

    filepath_abs = filepath if os.path.isabs(filepath) else os.path.abspath(
        os.path.join(state.session_cwd, filepath)
    )
    issue = detect_partial_symbol_redefinition(
        filepath=filepath_abs,
        start_line=start_line,
        end_line=end_line,
        replacement_content=replacement_content,
    )
    if issue is None:
        return TurnStatus.OK

    agent_flags.record_hit("partial_symbol_replacement", state.track_guardrail_hits)
    agent_flags.consecutive_errors += 1
    agent_flags.recovery_pending = "complete_symbol_edit"

    indent_description = (
        f"{issue.indentation_column} column(s)"
        if issue.indentation
        else "column 0 (module level)"
    )
    alert = (
        "SYSTEM ALERT: STRUCTURAL REPLACEMENT VIOLATION\n\n"
        "Your `replace_lines` operation was rejected before execution. No file changes were made.\n\n"
        f"The requested range ({issue.requested_start_line}-{issue.requested_end_line}) "
        f"overlaps only part of the existing {issue.symbol_kind} "
        f"`{issue.symbol_name}`, whose complete source range is "
        f"{issue.symbol_start_line}-{issue.symbol_end_line}.\n\n"
        f"Your replacement payload introduces another {issue.symbol_kind} "
        f"named `{issue.symbol_name}`. Applying it to the partial range would leave "
        "the original definition in place and risk creating an unintended nested or "
        "duplicate definition.\n\n"
        "REQUIRED ACTION:\n"
        f"1. Replace the complete existing symbol using start_line={issue.symbol_start_line} "
        f"and end_line={issue.symbol_end_line}.\n"
        f"2. Preserve the original definition's indentation ({indent_description}).\n"
        "3. Retry with `replace_lines`. Do not attempt to work around this alert by "
        "changing indentation while keeping the partial line range."
    )

    if agent_flags.consecutive_errors >= 3:
        print(
            "🛑 [Circuit Breaker] Repeated partial-symbol replacement attempts. "
            "Forcing turn end."
        )
        state.messages.append({"role": "user", "content": alert})
        return TurnStatus.END_TURN

    print(
        f"🛡️  [Guardrail] Rejected partial replacement of "
        f"{issue.symbol_kind} `{issue.symbol_name}` "
        f"({issue.requested_start_line}-{issue.requested_end_line}; "
        f"full range {issue.symbol_start_line}-{issue.symbol_end_line})."
    )
    state.messages.append({"role": "user", "content": alert})
    return TurnStatus.TRY_AGAIN
