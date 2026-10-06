import json
import re
from collections import Counter


# def _detect_repetition(significant_lines, window=80, threshold=6):
#     """
#     Line-level fallback: catches degenerate single-line spam loops.
#     Problematic: causes very common false-positives. Not worth keeping
#     """
#     recent = significant_lines[-window:]
#     if len(recent) < threshold:
#         return False
#     _, count = Counter(recent).most_common(1)[0]
#     return count >= threshold


def _detect_repetition(lines):
    """Extremely conservative version of the guardrail: only flag if SAME LINE repeats 50+ times in a row."""
    if len(lines) < 50:
        return False

    # Count only exact consecutive matches
    from collections import Counter
    freq = Counter(lines[-100:])  # Look at last 100 lines
    most_common, count = freq.most_common(1)[0]

    # Require 50+ identical lines to trigger (extremely rare)
    return count >= 50


def _extract_completed_payloads(normalized_content):
    """Yields the string content of each fully-closed ```json {...} ``` block."""
    for match in re.finditer(r"```json\s*\n(.*?)\n```", normalized_content, re.DOTALL):
        try:
            payload = json.loads(match.group(1))
            if "args" in payload and "content" in payload.get("args", {}):
                yield payload["args"]["content"]
        except json.JSONDecodeError:
            continue

def check_context_guardrail(messages, llm, limit):
    """Calculates tokens and warns on memory overload."""
    try:
        tokens = sum(len(llm.tokenize(m["content"].encode('utf-8'))) + 10 for m in messages)
        if tokens > limit:
            print(
                f"\n🚨 [MEMORY OVERLOAD]: Prompt size is {tokens} tokens (Limit: {limit}).\n   The agent will likely hallucinate... Consider using '/clear' or '--deep-ast'.")
        elif tokens > int(limit * 0.85):
            print(
                f"\n⚠️  [MEMORY WARNING]: Approaching context limit ({tokens}/{limit} tokens, {(tokens / limit) * 100:.1f}%).")
    except Exception:
        pass
