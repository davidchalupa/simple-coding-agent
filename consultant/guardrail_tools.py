import json
import re


def check_context_guardrail(current_messages, model, limit):
    """Calculates tokens and warns on memory overload."""
    try:
        tokens = sum(len(model.tokenize(m["content"].encode('utf-8'))) + 10 for m in current_messages)
        if tokens > limit:
            print(
                f"\n🚨 [MEMORY OVERLOAD]: Prompt size is {tokens} tokens (Limit: {limit}).\n   The consultant will likely hallucinate... Consider using '/clear'.")
        elif tokens > int(limit * 0.85):
            print(
                f"\n⚠️  [MEMORY WARNING]: Approaching context limit ({tokens}/{limit} tokens, {(tokens / limit) * 100:.1f}%).")
    except Exception:
        pass


def fuzzy_extract_tool_calls(text):
    """
    Hunts for any valid JSON object containing 'name' and 'args',
    regardless of how the LLM wrapped, separated, or formatted them.
    """
    tools = []
    # Find every starting position of what looks like a tool call
    start_indices = [m.start() for m in re.finditer(r'\{\s*"name"\s*:', text)]

    for start_idx in start_indices:
        brace_count = 0
        in_string = False
        escape = False

        for i in range(start_idx, len(text)):
            char = text[i]

            if escape:
                escape = False
                continue

            if char == '\\':
                escape = True
            elif char == '"':
                in_string = not in_string
            elif not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1

                    if brace_count == 0:
                        # Reached the end of this specific JSON object
                        json_str = text[start_idx:i + 1]
                        try:
                            parsed = json.loads(json_str)
                            if "name" in parsed:
                                tools.append(parsed)
                        except json.JSONDecodeError:
                            pass
                        break
    return tools
