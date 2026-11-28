import os
import json

from coding_agent.guardrails.common import normalise_verification_path


def check_and_handle_identical_write(tool_args, state, agent_flags, content_key):
    """
    Checks if the proposed content is identical to the existing content in the file.
    If identical, blocks the write operation and provides a guardrail message.
    Returns (intercepted: bool, should_break: bool)
    """
    target_fp = tool_args.get("filepath", "")
    if not target_fp or not os.path.isfile(target_fp):
        return False, False

    try:
        with open(target_fp, "r", encoding="utf-8") as f:
            existing_disk_content = f.read()

        proposed_content = tool_args.get(content_key, "")

        # 1. TIGHTER COMPARISON: Normalize line endings and trailing line spaces
        def normalize_code(code_str):
            code_str = code_str.replace('\r\n', '\n')
            return "\n".join(line.rstrip() for line in code_str.splitlines()).strip()

        if normalize_code(existing_disk_content) == normalize_code(proposed_content):
            print(
                f"🛡️  [Guardrail] Blocked identical write_file to '{os.path.basename(target_fp)}' (No-Op Regurgitation).")
            agent_flags.record_hit("identical_write", state.track_guardrail_hits)

            agent_flags.consecutive_errors += 1
            if agent_flags.consecutive_errors >= 3:
                print("🛑 [Circuit Breaker] Agent stuck in identical write loop. Forcing turn end.")
                return True, True  # intercepted, SHOULD BREAK

            # 2. PATH NORMALIZATION: Ensure relative/absolute paths match correctly
            target_abs = os.path.abspath(target_fp)
            last_fail = agent_flags.last_verification_failure or {}
            last_fail_abs = os.path.abspath(last_fail.get("filepath", "")) if last_fail.get("filepath") else ""

            # 3. THE "JOLT" MESSAGES: Force the agent to realize it didn't change the payload
            if last_fail_abs == target_abs:
                alert_msg = (
                    f"System Alert: `write_file` blocked (No-Op Regurgitation).\n"
                    f"You attempted to overwrite '{target_fp}' with the EXACT SAME BROKEN CODE that just failed self-verification.\n"
                    f"Although you may have diagnosed the problem in your text, you failed to actually change the tool payload.\n\n"
                    f"Error was:\n{last_fail.get('error', '')}\n\n"
                    f"ACTION REQUIRED: Do not resubmit the same file. Actually implement the fix and issue a new tool call."
                )
                if "unterminated string literal" in last_fail.get("error", ""):
                    alert_msg += "\n\nHint: If you used '\\n' inside a Python string, the JSON parser converted it to a real newline. Avoid using '\\n' in string literals (e.g. use multiple prints) or quadruple-escape it as `\\\\n`."

            elif getattr(agent_flags, "awaiting_fix", False) and getattr(agent_flags, "last_run_cmd_error", None):
                alert_msg = (
                    f"System Alert: `write_file` blocked (No-Op Regurgitation).\n"
                    f"You submitted code that is IDENTICAL to the file that just failed with this error:\n{agent_flags.last_run_cmd_error}\n\n"
                    f"The file is NOT fixed. Do not claim success or say the file 'already contains the correct content'. "
                    f"You likely diagnosed the issue in your thoughts but forgot to apply the change to the `content` argument.\n"
                    f"ACTION REQUIRED: Make a REAL change to fix the error."
                )
                if any(sig in agent_flags.last_run_cmd_error for sig in ("ModuleNotFoundError", "ImportError")):
                    alert_msg += (
                        "\n\nHint: this is a missing-module error. Do not guess a module name by analogy to a "
                        "function name (e.g. assuming `foo_get_action` lives in `action_foo_agent.py`). "
                        "If you search the codebase for the function name, use a query like `def random_get_action` "
                        "(not just the bare name) to find the DEFINITION site specifically — a bare name search "
                        "will also match your own broken import line and may hide the real result. Also use a "
                        "higher max_matches (e.g. 5) since the same name can appear in multiple places."
                    )

            else:
                alert_msg = (
                    f"System Alert: `write_file` on '{target_fp}' was blocked because the new content is IDENTICAL to the existing file on disk.\n"
                    f"If the user only asked to read, analyze, inspect, or explain, DO NOT invoke write tools. Answer directly in plain text."
                )

            state.messages.append({
                "role": "user",
                "content": alert_msg
            })
            return True, False  # intercepted, keep retrying

    except Exception:
        pass

    return False, False


def check_and_handle_drastic_shrinkage(tool_args, state, agent_flags, content_key, threshold=0.5):
    """
    Blocks a write_file that removes more than `threshold` fraction of the existing file's
    lines, unless the file is trivially small. Catches 'fix one error by deleting everything'
    regressions that check_and_handle_identical_write cannot (content is NOT identical, so
    that guardrail doesn't fire — this one's job is drastic, not identical, changes).
    """
    target_fp = tool_args.get("filepath", "")
    if not os.path.isfile(target_fp):
        return False

    try:
        with open(target_fp, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()

        proposed_content = tool_args.get(content_key, "")
        proposed_lines = proposed_content.splitlines(keepends=True)

        if len(existing_lines) >= 5 and len(proposed_lines) < len(existing_lines) * (1 - threshold):
            print(
                f"🛡️  [Guardrail] Blocked drastic shrinkage of '{os.path.basename(target_fp)}' "
                f"({len(existing_lines)} → {len(proposed_lines)} lines)."
            )

            agent_flags.consecutive_errors += 1
            if agent_flags.consecutive_errors >= 3:
                print("🛑 [Circuit Breaker] Agent stuck attempting to shrink the file. Forcing turn end.")
                return True

            alert_msg = (
                f"System Alert: `write_file` blocked — this write would shrink '{target_fp}' from "
                f"{len(existing_lines)} lines to {len(proposed_lines)} lines "
                f"({len(existing_lines) - len(proposed_lines)} lines removed). "
                f"If you are fixing a specific error (e.g. a bad import), make a SCOPED fix — "
                f"correct only the broken line(s) and preserve all existing test methods and "
                f"logic. Do not delete content while fixing an unrelated error. If you intend "
                f"to genuinely replace most of the file, explain why in your reasoning first."
            )
            state.messages.append({"role": "user", "content": alert_msg})
            return True
    except Exception:
        pass
    return False


def check_and_handle_loop_guardrail(tool_name, tool_args, state, agent_flags):
    """
    Detects repeated identical calls and alternating read-only tool loops.
    Returns (intercepted: bool, should_break: bool).
    """
    if tool_name in {"read_file", "read_symbol"}:
        norm_path = normalise_verification_path(tool_args.get("filepath"), state)
        pending = getattr(agent_flags, "post_edit_verification_pending", set())
        if norm_path and norm_path in pending:
            return False, False

    HARD_LOOP_REPEAT_COUNT = 4

    curr_sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
    agent_flags.recent_tool_signatures.append(curr_sig)

    # Keep enough history to detect a repeated two-call cycle.
    agent_flags.recent_tool_signatures = agent_flags.recent_tool_signatures[-8:]

    history = agent_flags.recent_tool_signatures

    # Preserve the existing identical-call detection window.
    repeat_count = history[-6:].count(curr_sig)

    if repeat_count >= HARD_LOOP_REPEAT_COUNT:
        agent_flags.record_hit("loop_guardrail", state.track_guardrail_hits)

        print(
            "🛑 [Circuit Breaker] Persistent identical tool-call loop detected. "
            "Forcing turn end."
        )

        state.messages.append({
            "role": "user",
            "content": (
                f"CRITICAL SYSTEM INTERVENTION: Identical '{tool_name}' calls were "
                f"repeated {repeat_count} times and tool execution is now halted. "
                "Do not call any more tools. Provide your final response based on "
                "the tool outputs already in the conversation, or explain any "
                "unresolved blocker in plain text."
            )
        })
        return True, True

    # Detect four repetitions of an alternating read_file/read_symbol sequence.
    if len(history) >= 8:
        last_eight = history[-8:]
        first, second = last_eight[:2]

        is_alternating_cycle = (first != second and last_eight == [first, second] * 4)

        read_only_tools = {"read_file", "read_symbol"}

        cycle_tool_names = {
            signature.split(":", 1)[0]
            for signature in (first, second)
        }

        if (is_alternating_cycle and cycle_tool_names.issubset(read_only_tools) and len(cycle_tool_names) == 2):
            agent_flags.record_hit("alternating_read_loop", state.track_guardrail_hits)

            print(
                "🛑 [Circuit Breaker] Repeated alternating read-only tool cycle "
                "detected. Forcing turn end."
            )

            state.messages.append({
                "role": "user",
                "content": (
                    "CRITICAL SYSTEM INTERVENTION: You are repeatedly alternating "
                    "between read_file and read_symbol without making progress. "
                    "Stop repeating inspections. Review the outstanding error "
                    "and identify the actual corrective action required. "
                    "If you cannot determine the fix, explain the blocker in plain text."
                )
            })
            return True, True

    # Soft interception
    if repeat_count >= 2:
        agent_flags.record_hit("loop_guardrail", state.track_guardrail_hits)
        print(f"🔁 [Loop Guardrail] Blocked repeated '{tool_name}' call (seen {repeat_count}x in recent history).")

        state.messages.append({
            "role": "user",
            "content": (
                f"System Alert: This exact '{tool_name}' call has already been made "
                f"{repeat_count} times recently and was blocked by the loop guardrail "
                "to prevent redundant execution. This block does NOT mean the previous "
                "run failed.\n"
                "Review the earlier tool output already in the conversation. "
                "If it already answers the request or shows the tests passing, "
                "stop calling tools and respond with your final answer. Only run a "
                "different command or edit if you have a specific, concrete reason "
                "the prior output is incomplete or wrong."
            )
        })
        return True, False

    return False, False
