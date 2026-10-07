def get_tokens_used(messages, llm):
    return sum(len(llm.tokenize(m["content"].encode('utf-8'))) + 10 for m in messages)


def render_token_footer(messages, llm, total_budget):
    """Render response with token budget footer"""
    tokens_used = get_tokens_used(messages, llm)
    usage_pct = (tokens_used / total_budget * 100)

    status_color = "🟢" if usage_pct < 80 else "🟡" if usage_pct < 95 else "🔴"

    print(
        f"\n{status_color} Tokens used: {tokens_used}/{total_budget} ({usage_pct:.1f}%)")
