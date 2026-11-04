import os


def get_tokens_used(messages, llm):
    return sum(len(llm.tokenize(m["content"].encode('utf-8'))) + 10 for m in messages)


def get_token_footer(messages, llm, total_budget):
    """Get response with token budget footer"""
    tokens_used = get_tokens_used(messages, llm)
    usage_pct = (tokens_used / total_budget * 100)

    status_color = "🟢" if usage_pct < 80 else "🟡" if usage_pct < 95 else "🔴"

    return f"\n{status_color} Tokens used: {tokens_used}/{total_budget} ({usage_pct:.1f}%)"


def render_token_footer(messages, llm, total_budget):
    print(get_token_footer(messages, llm, total_budget))


def prune_stale_reads(messages: list, tool_name: str, tool_args: dict, tool_result: str):
    """
    Supersedes older read_symbol or full read_file outputs in state.messages
    to prevent attention dilution and context bloat for small models.
    """
    if tool_result.startswith("Error"):
        return

    if tool_name == "read_symbol":
        symbol_name = tool_args.get("symbol_name")
        filepath = tool_args.get("filepath")
        if not symbol_name:
            return

        for msg in messages:
            if msg.get("role") == "user" and "Tool Execution Result:" in msg.get("content", ""):
                content = msg["content"]
                if f"Symbol: '{symbol_name}'" in content or f'Symbol: "{symbol_name}"' in content:
                    if filepath and os.path.basename(filepath) not in content:
                        continue
                    msg["content"] = (
                        f"Tool Execution Result:\n"
                        f"[Superseded: A newer read_symbol for '{symbol_name}' was performed later "
                        f"in this session. Full content omitted to save context.]"
                    )
                    print(f"🧹 [Context Hygiene] Superseded stale read_symbol for '{symbol_name}'")

    elif tool_name == "read_file":
        filepath = tool_args.get("filepath")
        max_lines = tool_args.get("max_lines", 100)
        if not filepath:
            return

        try:
            is_full_read = int(max_lines) == -1
        except (ValueError, TypeError):
            is_full_read = False

        if not is_full_read:
            return

        filename = os.path.basename(filepath)

        for msg in messages:
            if msg.get("role") == "user" and "Tool Execution Result:" in msg.get("content", ""):
                content = msg["content"]
                if f"--- File: {filepath}" in content or f"--- File: {filename}" in content:
                    msg["content"] = (
                        f"Tool Execution Result:\n"
                        f"[Superseded: A newer full read_file for '{filename}' was performed later "
                        f"in this session. Full content omitted to save context.]"
                    )
                    print(f"🧹 [Context Hygiene] Superseded stale full read_file for '{filename}'")
