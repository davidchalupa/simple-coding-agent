import re

from coding_agent.types import TurnStatus


def find_last_code_block(messages):
    """Scan backwards through assistant turns for the most recent fenced code block."""
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            match = re.search(r"```(?:python)?\s*\n(.*?)\n```", msg["content"], re.DOTALL)
            if match:
                return match.group(1)
    return None


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
