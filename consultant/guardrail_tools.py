import json
import re
import ast


def fuzzy_extract_tool_calls(text):
    """
    Hunts for any valid JSON object containing 'name' and 'args',
    regardless of how the LLM wrapped, separated, or formatted them.
    """
    tools = []
    # Tweaked regex: catches both "name" and 'name'
    start_indices = [m.start() for m in re.finditer(r'\{\s*["\']name["\']\s*:', text)]

    for start_idx in start_indices:
        brace_count = 0
        in_string = False
        escape = False
        string_char = None # track if we are in a ' or " string

        for i in range(start_idx, len(text)):
            char = text[i]

            if escape:
                escape = False
                continue

            if char == '\\':
                escape = True
            elif char in ('"', "'"):
                # Handle entering/exiting strings with correct quote type
                if not in_string:
                    in_string = True
                    string_char = char
                elif string_char == char:
                    in_string = False
            elif not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1

                    if brace_count == 0:
                        json_str = text[start_idx:i + 1]
                        try:
                            # Try standard JSON first
                            parsed = json.loads(json_str)
                            if "name" in parsed:
                                tools.append(parsed)
                        except json.JSONDecodeError:
                            # Fallback: Model used single quotes or trailing commas
                            try:
                                parsed = ast.literal_eval(json_str)
                                if isinstance(parsed, dict) and "name" in parsed:
                                    tools.append(parsed)
                            except (ValueError, SyntaxError):
                                pass
                        break
    return tools


# --- Sanitization for models that emit reasoning traces / chat-template leakage ---
# (e.g. DeepSeek-R1-Distill-Qwen). No-op for models that never produce these,
# such as Qwen2.5-Coder, so behavior there is unchanged.
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
STRAY_TOKEN_RE = re.compile(r"<\|im_start\|>\s*assistant\s*|<\|im_end\|>|<\|im_start\|>")

def sanitize_response(response_content: str) -> str:
    """Strip reasoning blocks and leaked chat-template tokens before the
    content is parsed for tool calls or stored in message history."""
    cleaned = THINK_BLOCK_RE.sub("", response_content)
    if "<think>" in cleaned and "</think>" not in cleaned:
        # Truncated mid-thought (e.g. hit a stop condition) — drop everything
        # from the opening <think> tag onward, since it's unfinished reasoning.
        cleaned = cleaned.split("<think>")[0]
    cleaned = STRAY_TOKEN_RE.sub("", cleaned)
    return cleaned.strip()


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


# NOTE: max_output_tokens below must track the max_tokens value passed to
# create_chat_completion() in common/output_handler.py's stream_agent_response
# — the output budget shares the same context window as the input on
# llama.cpp, so an out-of-sync value here will under- or over-estimate the
# real headroom available for evidence.
DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_SAFETY_MARGIN_TOKENS = 256


def _count_tokens(llm, text: str) -> int:
    if not text:
        return 0
    try:
        return len(llm.tokenize(text.encode("utf-8")))
    except Exception:
        # Fallback if tokenize() ever misbehaves on odd input — a rough
        # char/4 estimate is safer than crashing the whole diagnose turn.
        return len(text) // 4


def build_trimmed_evidence_block(
    llm,
    context_window,
    question,
    system_prompt,
    gathered_text,
    cached_entries,
    max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    safety_margin_tokens=DEFAULT_SAFETY_MARGIN_TOKENS,
):
    """
    Assemble the /diagnose evidence block, trimming to fit the model's ACTUAL
    context window (which can differ turn to turn — e.g. a 14B model may land
    on 10240 instead of its registry max_context if larger sizes fail to
    allocate). Must be called only after the model is loaded, since it needs
    the real context_window and a working tokenizer via llm.tokenize().

    Priority order:
      1. This turn's freshly gathered evidence (gathered_text) is always
         kept in full — it's what the question is actually about. If it
         alone doesn't fit, we refuse rather than truncate mid-file, since a
         half-file diff/analysis is worse than an honest "too big" message.
      2. Cached entries (files loaded in earlier turns) fill whatever budget
         remains, most-recently-loaded first, dropping whole entries — never
         partial content — once the budget runs out.

    Returns (user_content, note):
      - On success: user_content is the full assembled prompt text, and note
        is either None (nothing was dropped) or a short string describing
        what was omitted, for the caller to print/log.
      - On failure (fresh evidence alone doesn't fit): user_content is None
        and note is a human-readable explanation — the caller should treat
        this as an error and skip generation entirely rather than attempt a
        doomed or truncated analysis.
    """
    header = f"DIAGNOSTIC QUESTION:\n{question}\n\nRETRIEVED EVIDENCE:\n"
    footer = (
        "\n\nIMPORTANT:\n"
        "The diagnostic question may contain a proposed explanation or "
        "hypothesis. Treat that as unverified. Determine the answer from "
        "the retrieved source code, logs, and other concrete evidence."
    )

    budget = context_window - max_output_tokens - safety_margin_tokens
    fixed_cost = _count_tokens(llm, system_prompt) + _count_tokens(llm, header) + _count_tokens(llm, footer)
    remaining = budget - fixed_cost

    fresh_block = (gathered_text or "").strip()
    fresh_cost = _count_tokens(llm, fresh_block)

    if fresh_cost > remaining:
        return None, (
            f"This turn's freshly gathered evidence needs about {fresh_cost} tokens, but only "
            f"~{max(remaining, 0)} are available in the reasoning model's context window "
            f"({context_window} total). Try asking about a more specific file, function, or "
            f"symbol instead of the whole file."
        )

    remaining -= fresh_cost
    included_cached = []
    dropped_labels = []

    for entry in cached_entries:
        entry_text = f"Cached {entry['tool_name']} result for {entry['label']}:\n{entry['content']}"
        entry_cost = _count_tokens(llm, entry_text)
        if entry_cost <= remaining:
            included_cached.append(entry_text)
            remaining -= entry_cost
        else:
            dropped_labels.append(entry['label'])

    parts = [fresh_block] if fresh_block else []
    parts.extend(included_cached)
    context_block = "\n\n".join(parts) if parts else \
        "(No context has been loaded yet this session — nothing to analyze.)"

    note = None
    if dropped_labels:
        note = f"{len(dropped_labels)} previously cached file(s) omitted for space: {', '.join(dropped_labels)}"
        context_block += (
            f"\n\n[NOTE: {note}. The analysis below may be incomplete with respect to those files.]"
        )

    user_content = header + context_block + footer
    return user_content, note
