import json
import re


def _is_example_context(text: str, match_start: int) -> bool:
    prefix = text[max(0, match_start - 300):match_start].lower()
    patterns = [
        r'\bfor example\b', r'\bfor instance\b', r'\ban example\b',
        r'\bhypothetical\b', r'\bfollowing\b.*\b(json|command|tool|call|format)\b',
        r'\byou can use\b', r'\bhere is how\b', r'\bhere\'s how\b',
        r'\blike this\b', r'\bas follows\b'
    ]
    return any(re.search(p, prefix) for p in patterns)


def _has_trailing_prose(text: str, match_end: int) -> bool:
    suffix = text[match_end:].strip()
    if not suffix:
        return False
    suffix = re.sub(r"<payload>.*?(?:</payload>|$)", "", suffix, flags=re.DOTALL)
    suffix = re.sub(r"```.*?```", "", suffix, flags=re.DOTALL)
    suffix = re.sub(r"`+", "", suffix)
    return bool(re.search(r'[a-zA-Z0-9]', suffix))


def _clean_over_escaped_quotes(text):
    return text.replace('\\"', '"') if isinstance(text, str) else text


def _extract_balanced_json_object(text, start_idx=0):
    start = text.find('{', start_idx)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return None


def _raw_string_arg(text, key):
    m = re.search(
        rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"',
        text,
        re.DOTALL,
    )
    return m.group(1) if m else None


def _fix_double_escaped_newlines(raw):
    """
    Detects the specific pathology where the model emitted \\n (JSON-decodes to literal
    backslash-n) everywhere a real newline was intended, rather than \n (JSON-decodes to
    an actual newline). Heuristic: three or more \\n sequences is treated as over-escaped
    source code rather than legitimate content that needs a literal backslash-n.

    IMPORTANT: \\n is valid JSON (it decodes cleanly to the two characters '\' and 'n'),
    so this must be checked BEFORE trusting a successful json.loads — a plain try/except
    around json.loads will never raise on this input and will silently return the wrong,
    over-escaped result.
    """
    if not isinstance(raw, str):
        return raw
    if raw.count(r'\\n') >= 3:
        return raw.replace(r'\\n', r'\n')
    return raw


def _decode_inline_content(raw):
    """
    Decode a raw JSON string value for file content, with two model-specific repairs:

    1. \\' (escaped single-quote) is never valid JSON — ' never needs escaping inside a
       JSON string. Unescape it in place. This does NOT toggle any string-tracking state;
       it's purely a "the model added an unnecessary/invalid escape" fix.

    2. Outside a nested double-quoted string in the generated source (tracked via \\"
       boundaries), a doubly-escaped \\\\n is treated as a structural line break and
       collapsed to a single \\n (which json.loads will turn into a real newline).
       INSIDE a nested \\"...\\" string, \\\\n is left as-is, since it likely represents
       a literal backslash-n escape sequence the generated file's own string needs to
       contain on disk (e.g. print("hello\\nworld") should keep \\n as two characters,
       not become an actual newline).

    Without repair (1), a single stray \\' anywhere in the payload causes the final
    json.loads() call to fail for the ENTIRE string, falling back to returning the raw,
    completely unconverted text — this was the actual root cause of prior corruption,
    not a flaw in the context-tracking approach itself.
    """
    if raw is None:
        return ""

    out = []
    i = 0
    in_string = False  # inside a \"..\" nested string literal in the generated source

    while i < len(raw):
        if raw.startswith(r"\'", i):
            out.append("'")
            i += 2
            continue

        if raw.startswith(r'\"', i):
            in_string = not in_string
            out.append(r'\"')
            i += 2
            continue

        if raw.startswith(r'\\n', i):
            out.append(r'\\n' if in_string else r'\n')
            i += 3
            continue

        out.append(raw[i])
        i += 1

    try:
        return json.loads(f'"{"".join(out)}"', strict=False)
    except json.JSONDecodeError:
        return raw


def _decode_string_field(raw):
    """Shared decode-with-repair for any raw JSON string field that may contain
    multi-line source content (old_content, new_content, etc.), not just `content`."""
    if raw is None:
        return None
    return _decode_inline_content(raw)


def extract_tool_call(response_content: str, allow_patch: bool = True) -> dict | None:
    tool_json_str = None
    search_start = 0

    m = re.search(r"<tool_call>(.*?)</tool_call>", response_content, re.DOTALL)
    if m:
        tool_json_str = m.group(1).strip()
        search_start = m.end()
    else:
        candidates = []
        for m in re.finditer(r"```json\s*\n(.*?)\n```", response_content, re.DOTALL):
            candidate = m.group(1).strip()
            if '"name"' not in candidate:
                continue
            if _is_example_context(response_content, m.start()):
                continue
            candidates.append((candidate, m.start(), m.end()))

        if candidates:
            if len(candidates) > 1:
                print(
                    f"⚠️  [Parser] {len(candidates)} JSON tool-call blocks found in one response; "
                    f"executing the FIRST only and ignoring the rest. The model should emit ONE "
                    f"tool call per turn."
                )
            # Only apply the trailing-prose check when there's a single candidate — its job is to
            # tell a real call apart from a documentation snippet, not to choose between two
            # otherwise-valid real calls. With multiple candidates, always prefer the first.
            if len(candidates) == 1:
                candidate, start, end = candidates[0]
                if not _has_trailing_prose(response_content, end):
                    tool_json_str, search_start = candidate, end
            else:
                candidate, start, end = candidates[0]
                tool_json_str, search_start = candidate, end

    if not tool_json_str:
        m = re.search(r'\{\s*"name"\s*:\s*"[^"]+"', response_content, re.DOTALL)
        if m and not _is_example_context(response_content, m.start()):
            balanced = _extract_balanced_json_object(response_content, m.start())
            if balanced:
                end = m.start() + len(balanced)
                if not _has_trailing_prose(response_content, end):
                    tool_json_str = balanced.strip()
                    search_start = end

    if not tool_json_str:
        return None

    return parse_robust_tool_call(
        response_content,
        tool_json_str,
        allow_patch=allow_patch,
        search_start=search_start,
    )


def parse_robust_tool_call(
    response_content,
    tool_json_str,
    allow_patch=True,
    search_start=0,
):
    remainder = response_content[search_start:]

    payload_match = re.search(
        r"<payload>(.*?)(?:</payload>|$)",
        remainder,
        re.DOTALL,
    )
    raw_payload = (
        payload_match.group(1).strip('\r\n')
        if payload_match else None
    )

    if raw_payload is None:
        payload_match = re.search(
            r"<payload>(.*?)(?:</payload>|$)",
            tool_json_str,
            re.DOTALL,
        )
        if payload_match:
            raw_payload = payload_match.group(1).strip('\r\n')

    if not raw_payload:
        m = re.search(r"```[a-zA-Z]*\n(.*?)\n```", remainder, re.DOTALL)
        if m:
            raw_payload = m.group(1)

    json_clean = re.sub(
        r"<payload>.*?(?:</payload>|$)",
        "",
        tool_json_str,
        flags=re.DOTALL,
    ).strip()

    # For file-content tools, decode the raw content field ourselves.
    # This preserves literal source "\\n" while fixing extra-escaped line breaks.
    raw_content = _raw_string_arg(json_clean, "content")

    try:
        data = json.loads(json_clean, strict=False)

        if "name" not in data or data.get("name") is None:
            raise json.JSONDecodeError(
                "Parsed JSON has no 'name' field.",
                json_clean,
                0,
            )

        if "args" not in data:
            data["args"] = {}

        name = data["name"]

        if name in ("write_file", "append_file", "replace_lines"):
            if raw_content is not None:
                data["args"]["content"] = _decode_inline_content(raw_content)
            elif not data["args"].get("content") and raw_payload is not None:
                data["args"]["content"] = raw_payload

        if name == "patch_file":
            raw_old = _raw_string_arg(json_clean, "old_content")
            raw_new = _raw_string_arg(json_clean, "new_content")
            if raw_old is not None:
                data["args"]["old_content"] = _decode_string_field(raw_old)
            if raw_new is not None:
                data["args"]["new_content"] = _decode_string_field(raw_new)

        for key in ("content", "old_content", "new_content"):
            if key in data["args"]:
                data["args"][key] = _clean_over_escaped_quotes(data["args"][key])

        return data

    except json.JSONDecodeError:
        pass

    cleaned = json_clean.strip()
    allowed = (
        "write_file|append_file|replace_lines|read_file|run_cmd|patch_file|"
        "extract_code_blocks|list_tree|search_codebase|read_symbol"
        if allow_patch else
        "write_file|append_file|read_file|run_cmd|extract_code_blocks|"
        "list_tree|search_codebase|read_symbol"
    )

    m = re.search(fr'"name"\s*:\s*"({allowed})"', cleaned)
    if not m:
        raise json.JSONDecodeError(
            "Could not isolate tool name signature from model string.",
            json_clean,
            0,
        )

    tool_name = m.group(1)
    args = {}

    if tool_name in ("write_file", "append_file"):
        fp = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp:
            args["filepath"] = fp.group(1)

        raw = _raw_string_arg(cleaned, "content")
        if raw is not None:
            args["content"] = _decode_inline_content(raw)
        elif raw_payload is not None:
            args["content"] = raw_payload
        else:
            args["content"] = ""

        return {"name": tool_name, "args": args}

    if tool_name == "replace_lines":
        fp = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        sl = re.search(r'"start_line"\s*:\s*(\d+)', cleaned)
        el = re.search(r'"end_line"\s*:\s*(\d+)', cleaned)

        if fp:
            args["filepath"] = fp.group(1)
        if sl:
            args["start_line"] = int(sl.group(1))
        if el:
            args["end_line"] = int(el.group(1))

        for key in ("expected_start_snippet", "expected_end_snippet"):
            value = _raw_string_arg(cleaned, key)
            if value is not None:
                args[key] = (
                    value.replace('\\"', '"')
                    .replace('\\\\', '\\')
                )

        raw = _raw_string_arg(cleaned, "content")
        if raw is not None:
            args["content"] = _decode_inline_content(raw)
        elif raw_payload is not None:
            args["content"] = raw_payload
        else:
            args["content"] = ""

        return {"name": tool_name, "args": args}

    if tool_name == "patch_file":
        fp = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        old = _raw_string_arg(cleaned, "old_content")
        new = _raw_string_arg(cleaned, "new_content")

        if fp:
            args["filepath"] = fp.group(1)
        if old is not None:
            args["old_content"] = _decode_string_field(old)
        if new is not None:
            args["new_content"] = _decode_string_field(new)

        return {"name": tool_name, "args": args}

    if tool_name == "run_cmd":
        value = _raw_string_arg(cleaned, "command")
        if value is not None:
            args["command"] = json.loads(f'"{value}"')
        return {"name": tool_name, "args": args}

    if tool_name == "read_file":
        fp = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        sl = re.search(r'"start_line"\s*:\s*(\d+)', cleaned)
        ml = re.search(r'"max_lines"\s*:\s*(\d+)', cleaned)
        if fp:
            args["filepath"] = fp.group(1)
        if sl:
            args["start_line"] = int(sl.group(1))
        if ml:
            args["max_lines"] = int(ml.group(1))
        return {"name": tool_name, "args": args}

    if tool_name == "list_tree":
        dp = re.search(r'"dir_path"\s*:\s*"(.*?)"', cleaned)
        md = re.search(r'"max_depth"\s*:\s*(\d+)', cleaned)
        if dp:
            args["dir_path"] = dp.group(1)
        if md:
            args["max_depth"] = int(md.group(1))
        return {"name": tool_name, "args": args}

    if tool_name == "search_codebase":
        dp = re.search(r'"dir_path"\s*:\s*"(.*?)"', cleaned)
        q = _raw_string_arg(cleaned, "query")
        rg = re.search(r'"is_regex"\s*:\s*(true|false)', cleaned, re.I)
        mm = re.search(r'"max_matches"\s*:\s*(\d+)', cleaned)
        if dp:
            args["dir_path"] = dp.group(1)
        if q is not None:
            args["query"] = json.loads(f'"{q}"')
        if rg:
            args["is_regex"] = rg.group(1).lower() == "true"
        if mm:
            args["max_matches"] = int(mm.group(1))
        return {"name": tool_name, "args": args}

    if tool_name == "extract_code_blocks":
        return {"name": tool_name, "args": json.loads(cleaned).get("args", {})}

    if tool_name == "read_symbol":
        fp = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        sym = _raw_string_arg(cleaned, "symbol_name")
        if fp:
            args["filepath"] = fp.group(1)
        if sym is not None:
            args["symbol_name"] = json.loads(f'"{sym}"')
        return {"name": tool_name, "args": args}

    raise json.JSONDecodeError(
        "Fallback pattern parser extraction failed.",
        json_clean,
        0,
    )
