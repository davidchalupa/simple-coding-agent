import re
import hashlib

from common.guardrail_tools import _detect_repetition, _extract_completed_payloads
from common.context_hygiene_utils import get_tokens_used

# Catches a single character (digit, letter, punctuation — anything) repeated
# this many times consecutively with no newline in between. This is
# deliberately independent of the newline-gated _detect_repetition() check
# below: a pathological run like a single long line of "0000...0" has no
# newlines in it at all, so the line-based check structurally cannot see it
# no matter how long it runs. This check runs on every chunk, unconditionally.
_CHAR_RUN_RE = re.compile(r"(.)\1{49,}")
# How many trailing characters of `content` to scan each time. Small and
# cheap — this runs once per streamed chunk — but large enough to catch a
# run that started slightly before the current chunk.
_CHAR_RUN_SCAN_WINDOW = 300


def _is_inside_code_block_or_table(content):
    lines = content.split('\n')
    in_code_block = False
    for line in reversed(lines[-20:]):
        if '```' in line and not line.strip().startswith('```'):
            continue
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = True
            break

    has_table_row = any(line.strip().startswith('|') and '|' in line
                        for line in lines[-10:])

    return in_code_block or has_table_row


def stream_agent_response(llm, messages,
                          context_window,
                          stop=None,
                          temperature=0.1,
                          repeat_penalty=1.1,
                          agent_label="\n[Agent]: ",
                          enforce_duplicate_payload_check=True,
                          max_output_tokens: int = None,
                          # === NEW: Output callbacks for GUI decoupling ===
                          on_chunk=None,
                          on_status=None,
                          stop_event=None):
    # Default console print if no callback provided
    def default_status(msg, end="\n", flush=False):
        print(msg, end=end, flush=flush)

    status_fn = on_status if on_status is not None else default_status

    status_fn(agent_label)
    content, finish_reason = "", None
    seen_payload_hashes = set()

    def handle_chunk(text):
        nonlocal content
        content += text
        if on_chunk is not None:
            on_chunk(text)
        else:
            print(text, end="", flush=True)

    try:
        max_output_tokens_by_context_window = int(0.2 * context_window)

        if max_output_tokens is None or max_output_tokens > max_output_tokens_by_context_window:
            max_output_tokens = min(max_output_tokens_by_context_window, context_window - get_tokens_used(messages, llm))

        for chunk in llm.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=temperature,
                repeat_penalty=repeat_penalty,
                max_tokens=max_output_tokens,
                stop=stop or []
        ):
            # === NEW: Check if Stop button was pressed ===
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt("User requested stop")

            choice = chunk['choices'][0]
            finish_reason = choice.get('finish_reason') or finish_reason
            if 'content' in (delta := choice.get('delta', {})):
                new_text = delta['content']
                handle_chunk(new_text)

                if not _is_inside_code_block_or_table(content):
                    if _CHAR_RUN_RE.search(content[-_CHAR_RUN_SCAN_WINDOW:]):
                        status_fn("\n\n🛑 [System]: Runaway character repetition detected. Forcing halt.")
                        finish_reason = "repetition_loop"
                        break

                is_real_newline = '\n' in new_text
                is_escaped_newline = '\\n' in new_text or (
                        new_text == 'n' and len(content) >= 2 and content[-2:] == '\\n')

                if is_real_newline or is_escaped_newline:
                    normalized_content = content.replace('\\n', '\n')
                    significant_lines = [
                        line.strip() for line in normalized_content.split('\n')
                        if len(line.strip()) > 10
                    ]
                    if _detect_repetition(significant_lines):
                        status_fn("\n\n🛑 [System]: Repetition loop detected. Forcing halt.")
                        finish_reason = "repetition_loop"
                        break

                if enforce_duplicate_payload_check:
                    if "```json" in content and content.rstrip().endswith("```"):
                        payloads = _extract_completed_payloads(content)
                        for payload_content in payloads:
                            h = hashlib.sha256(payload_content.encode('utf-8')).hexdigest()
                            if h in seen_payload_hashes:
                                status_fn("\n\n🛑 [System]: Duplicate payload detected. Forcing halt.")
                                finish_reason = "repetition_loop"
                                break
                            seen_payload_hashes.add(h)
                        if finish_reason == "repetition_loop":
                            break

    except KeyboardInterrupt:
        status_fn("\n\n🛑 [Generation Interrupted by User]")
        if "<tool_call> " in content and "</tool_call>" not in content:
            content = re.sub(r"<tool_call>.*$", "", content, flags=re.DOTALL).strip()
        if content: messages.append({"role": "assistant", "content": content + " [Interrupted]"})
        return content, False, True

    if "<tool_call>" in content and "</tool_call>" not in content:
        content += "</tool_call>"
        handle_chunk("</tool_call>")

    status_fn("")  # Final newline
    messages.append({"role": "assistant", "content": content})
    return content, (finish_reason == "length"), False
