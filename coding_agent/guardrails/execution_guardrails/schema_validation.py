from coding_agent.guardrails.definitions import TOOL_FORBIDDEN_FIELDS, TOOL_REQUIRED_FIELDS
from coding_agent.types import TurnStatus

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
