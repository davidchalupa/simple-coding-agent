import json
import re
from collections import Counter
from common.context_hygiene_utils import get_tokens_used


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
        tokens = get_tokens_used(messages, llm)

        # Critical Token Budget Enforcement ===
        if tokens > int(limit * 0.9):
            print(f"\n🚨 [CRITICAL TOKEN BUDGET]: {tokens}/{limit} ({(tokens / limit) * 100:.1f}%). "
                  f"Injecting system directive to enforce brevity.")

            # Inject critical instruction as user message (follows existing pattern)
            messages.append({
                "role": "user",
                "content": "CRITICAL: Token budget nearly exhausted. Provide only essential information now. "
                           "Use concise diffs, avoid re-printing full files, and answer directly."
            })

        if tokens > limit:
            print(
                f"\n🚨 [MEMORY OVERLOAD]: Prompt size is {tokens} tokens (Limit: {limit}).\n   The agent will likely hallucinate... Consider using '/clear' or '--deep-ast'.")
        elif tokens > int(limit * 0.85):
            print(
                f"\n⚠️  [MEMORY WARNING]: Approaching context limit ({tokens}/{limit} tokens, {(tokens / limit) * 100:.1f}%).")
    except Exception:
        pass


BACKTICK_SPAN_RE = re.compile(r'`[^`]*`')


def is_pure_load_request(user_input: str) -> bool:
    """Checks if the user prompt is strictly requesting to load/read context without asking a task."""
    # Strip backtick-quoted spans (filenames, symbols, paths) before keyword
    # matching. Otherwise a quoted identifier that happens to contain a task
    # word — e.g. `test_..._refactor.py` — falsely triggers the task-keyword
    # check and misclassifies a plain load request as a task request, which
    # then tells the model to "provide the complete solution directly" and
    # it dumps the whole file back instead of just acknowledging the load.
    stripped = BACKTICK_SPAN_RE.sub(' ', user_input)
    lowered = stripped.strip().lower()
    load_keywords = ("load ", "read ", "bring ", "fetch ", "show ")
    task_keywords = ("how", "why", "change", "refactor", "modify", "add", "fix", "update", "draft", "write", "create",
                     "implement", "start")

    is_load_cmd = any(lowered.startswith(kw) or f" {kw}" in lowered for kw in load_keywords) or (
        "into context" in lowered)
    has_task_cmd = any(kw in lowered for kw in task_keywords) or "?" in lowered

    return is_load_cmd and not has_task_cmd


def is_pure_load_or_inspect_request(user_input: str) -> bool:
    """
    Checks if the user prompt is strictly requesting to read or inspect
    via tools (like read_file, read_symbol) without asking for a code rewrite.
    """
    lowered_raw = user_input.strip().lower()

    # Explicitly check for tool inspection intents before backtick stripping
    is_tool_inspection = any(tool in lowered_raw for tool in ("read_file", "read_symbol", "inspect"))

    stripped = BACKTICK_SPAN_RE.sub(' ', user_input)
    lowered = stripped.strip().lower()

    inspect_keywords = ("read ", "inspect ", "examine ", "check ")
    task_keywords = ("change", "refactor", "modify", "add", "fix", "update", "draft", "write", "create", "implement",
                     "start")

    has_inspect_intent = any(
        lowered.startswith(kw) or f" {kw}" in lowered for kw in inspect_keywords) or is_tool_inspection
    has_task_cmd = any(kw in lowered for kw in task_keywords) or "?" in lowered

    return has_inspect_intent and not has_task_cmd
