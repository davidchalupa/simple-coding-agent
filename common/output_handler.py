import re
import hashlib

from common.guardrail_tools import _detect_repetition, _extract_completed_payloads

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


def stream_agent_response(llm, messages, stop=None, temperature=0.1, repeat_penalty=1.1, agent_label="\n[Agent]: "):
    print(agent_label, end="", flush=True)
    content, finish_reason = "", None
    seen_payload_hashes = set()

    try:
        for chunk in llm.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=temperature,
                repeat_penalty=repeat_penalty,
                max_tokens=4096,
                stop=stop or []
        ):
            choice = chunk['choices'][0]
            finish_reason = choice.get('finish_reason') or finish_reason
            if 'content' in (delta := choice.get('delta', {})):
                new_text = delta['content']
                print(new_text, end="", flush=True)
                content += new_text

                # --- NEWLINE-INDEPENDENT CHARACTER-RUN CHECK ---
                # Runs every chunk, regardless of newlines or formatting.
                # Catches degenerate single-character runaway generation
                # (e.g. a wall of repeated digits) that the line-based and
                # JSON-block checks below cannot see by construction.
                if _CHAR_RUN_RE.search(content[-_CHAR_RUN_SCAN_WINDOW:]):
                    print("\n\n🛑 [System]: Runaway character repetition detected. Forcing halt.")
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
                        print("\n\n🛑 [System]: Repetition loop detected. Forcing halt.")
                        finish_reason = "repetition_loop"
                        break

                # --- BLOCK-LEVEL DUPLICATE PAYLOAD CHECK ---
                # Catches "same code, different narration" retries that the
                # line-frequency detector structurally can't see, since each
                # retry's internal lines are individually unique even though
                # the whole payload is an exact repeat.
                if "```json" in content and content.rstrip().endswith("```"):
                    for payload_content in _extract_completed_payloads(content):
                        h = hashlib.sha256(payload_content.encode('utf-8')).hexdigest()
                        if h in seen_payload_hashes:
                            print("\n\n🛑 [System]: Duplicate payload detected. Forcing halt.")
                            finish_reason = "repetition_loop"
                            break
                        seen_payload_hashes.add(h)
                    if finish_reason == "repetition_loop":
                        break

    except KeyboardInterrupt:
        print("\n\n🛑 [Generation Interrupted by User]")
        if "<tool_call> " in content and "</tool_call>" not in content:
            content = re.sub(r"<tool_call>.*$", "", content, flags=re.DOTALL).strip()
        if content: messages.append({"role": "assistant", "content": content + " [Interrupted]"})
        return content, False, True

    if "<tool_call>" in content and "</tool_call>" not in content:
        content += "</tool_call>"
        print("</tool_call>", end="", flush=True)

    print()
    messages.append({"role": "assistant", "content": content})
    return content, (finish_reason == "length"), False
