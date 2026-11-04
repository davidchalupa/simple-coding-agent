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


def prune_stale_symbol_reads(messages: list, tool_name: str, tool_args: dict):
    """
    Supersedes older read_symbol outputs for the exact same symbol name
    in state.messages to prevent attention dilution and context bloat for small models.
    """
    if tool_name != "read_symbol":
        return

    symbol_name = tool_args.get("symbol_name")
    filepath = tool_args.get("filepath")
    if not symbol_name:
        return

    for msg in messages:
        if msg.get("role") == "user" and "Tool Execution Result:" in msg.get("content", ""):
            content = msg["content"]
            # Match the standard read_symbol header format
            if f"Symbol: '{symbol_name}'" in content or f'Symbol: "{symbol_name}"' in content:
                # If filepath is provided, ensure it matches the same file
                if filepath and os.path.basename(filepath) not in content:
                    continue

                msg["content"] = (
                    f"Tool Execution Result:\n"
                    f"[Superseded: A newer read_symbol for '{symbol_name}' was performed later "
                    f"in this session. Full content omitted to save context.]"
                )
