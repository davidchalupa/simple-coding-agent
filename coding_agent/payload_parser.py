import json
import re


def _normalize_double_escaped_content(text):
    """
    Some models JSON-escape their own intended escape sequences (writing \\n
    instead of \n inside the JSON string), so after a normal json.loads() pass
    the text still contains literal backslash-n instead of a real newline.
    Detect that pattern and fix it.
    """
    if not isinstance(text, str):
        return text
    if '\n' in text:
        # Already has real newlines -- content is fine as-is, don't touch it.
        return text
    if '\\n' not in text and '\\t' not in text and '\\r' not in text:
        return text
    return (
        text.replace('\\r\\n', '\n')
            .replace('\\n', '\n')
            .replace('\\t', '\t')
            .replace('\\r', '\n')
    )


def _clean_over_escaped_quotes(text):
    """
    After json.loads() has already decoded standard JSON escapes once, any
    remaining literal backslash-quote sequence is virtually always the model
    double-escaping (e.g. \\\" instead of \"). Collapse it.
    """
    if not isinstance(text, str):
        return text
    return text.replace('\\"', '"')


def _extract_balanced_json_object(text, start_idx=0):
    """
    Finds the first top-level JSON object substring starting at or after start_idx,
    correctly skipping over any { or } characters that appear INSIDE JSON string
    values (e.g. Python code with dict/set literals embedded in a "content" field).
    Returns the substring including the outer braces, or None if unbalanced/truncated.
    """
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


def extract_tool_call(response_content: str, allow_patch: bool = True) -> dict | None:
    """
    Model-agnostic wrapper that isolates candidate tool JSON strings
    and passes them to the hyper-robust parser without altering internal mechanics.
    """
    tool_json_str = None
    search_start = 0  # where to start looking for <payload> after this

    tool_match = re.search(r"<tool_call>(.*?)</tool_call>", response_content, re.DOTALL)
    if tool_match:
        tool_json_str = tool_match.group(1).strip()
        search_start = tool_match.end()
    else:
        for md_match in re.finditer(r"```json\s*\n(.*?)\n```", response_content, re.DOTALL):
            candidate = md_match.group(1).strip()
            if '"name"' in candidate:
                tool_json_str = candidate
                search_start = md_match.end()
                break

    if not tool_json_str:
        naked_start_match = re.search(r'\{\s*"name"\s*:\s*"[^"]+"', response_content, re.DOTALL)
        if naked_start_match:
            balanced = _extract_balanced_json_object(response_content, naked_start_match.start())
            if balanced:
                tool_json_str = balanced.strip()
                search_start = naked_start_match.start() + len(balanced)

    if not tool_json_str:
        return None

    return parse_robust_tool_call(response_content, tool_json_str, allow_patch=allow_patch, search_start=search_start)


# --- HYPER-ROBUST PAYLOAD PARSER ---
def parse_robust_tool_call(response_content, tool_json_str, allow_patch=True, search_start=0):
    remainder = response_content[search_start:]

    payload_match = re.search(r"<payload>(.*?)(?:</payload>|$)", remainder, re.DOTALL)
    raw_payload = payload_match.group(1).strip('\r\n') if payload_match else None

    # NEW: the model sometimes embeds <payload> inside the same fence as the tool call
    # itself, so the outer search_start-anchored lookup finds nothing. Check tool_json_str too.
    if raw_payload is None:
        inline_payload_match = re.search(r"<payload>(.*?)(?:</payload>|$)", tool_json_str, re.DOTALL)
        if inline_payload_match:
            raw_payload = inline_payload_match.group(1).strip('\r\n')

    if not raw_payload:
        md_block_match = re.search(r"```[a-zA-Z]*\n(.*?)\n```", remainder, re.DOTALL)
        if md_block_match:
            raw_payload = md_block_match.group(1)

    json_clean = re.sub(r"<payload>.*?(?:</payload>|$)", "", tool_json_str, flags=re.DOTALL).strip()

    # print(f"\n🔬 [DEBUG] json_clean repr (first 500 chars):\n{json_clean[:500]!r}\n")

    try:
        data = json.loads(json_clean, strict=False)
        if "name" not in data or data.get("name") is None:
            raise json.JSONDecodeError(
                "Parsed JSON has no 'name' field — likely matched the wrong block.",
                json_clean, 0
            )
        if "args" not in data:
            data["args"] = {}

        needs_content = data.get("name") in ["write_file", "append_file", "replace_lines"]
        has_inline_content = (
            needs_content
            and isinstance(data["args"].get("content"), str)
            and data["args"]["content"] != ""
        )

        if needs_content and not has_inline_content:
            data["args"]["content"] = raw_payload if raw_payload is not None else ""

        for key in ("content", "old_content", "new_content"):
            if key in data["args"]:
                data["args"][key] = _normalize_double_escaped_content(data["args"][key])
                data["args"][key] = _clean_over_escaped_quotes(data["args"][key])

        return data
    except json.JSONDecodeError:
        # print("🔬 [DEBUG] json.loads FAILED, falling through to regex parser")
        pass

    cleaned = json_clean.strip()

    allowed_tools = "write_file|append_file|replace_lines|read_file|run_cmd|patch_file|extract_code_blocks|list_tree|search_codebase|read_symbol" if allow_patch else "write_file|append_file|read_file|run_cmd|extract_code_blocks|list_tree|search_codebase|read_symbol"

    name_match = re.search(fr'"name"\s*:\s*"({allowed_tools})"', cleaned)

    if not name_match:
        raise json.JSONDecodeError("Could not isolate tool name signature from model string.", json_clean, 0)

    tool_name = name_match.group(1)
    args = {}

    if tool_name in ["write_file", "append_file"]:
        fp_match = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp_match:
            args["filepath"] = fp_match.group(1)

        content_match = re.search(r'"content"\s*:\s*"', cleaned)
        if content_match:
            start_idx = content_match.end()
            end_match = re.search(r'"\s*\}\s*\}\s*$', cleaned) or re.search(r'"\s*\}\s*$', cleaned)
            if end_match:
                raw_content_slice = cleaned[start_idx:end_match.start()]
            else:
                raw_tail = cleaned[start_idx:].rstrip(' \n\t}')
                if raw_tail.endswith('"'): raw_tail = raw_tail[:-1]
                raw_content_slice = raw_tail

            try:
                # json.loads does the ONE correct decode pass. Do NOT run
                # _normalize_double_escaped_content or _clean_over_escaped_quotes
                # after this -- they would re-process already-correct content and
                # corrupt legitimate \n escapes meant to stay literal in the source.
                args["content"] = json.loads(f'"{raw_content_slice}"')
            except json.JSONDecodeError:
                # Only use the manual replace + normalization path if the slice
                # wasn't valid standalone JSON-string content.
                args["content"] = (
                    raw_content_slice
                    .replace('\\"', '"')
                    .replace('\\\\', '\\')
                )
                args["content"] = _normalize_double_escaped_content(args["content"])
                args["content"] = _clean_over_escaped_quotes(args["content"])
        elif raw_payload is not None:
            args["content"] = raw_payload
        else:
            args["content"] = ""

        if "filepath" in args:
            return {"name": tool_name, "args": args}

    elif tool_name == "replace_lines":
        fp_match = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp_match: args["filepath"] = fp_match.group(1)
        sl_match = re.search(r'"start_line"\s*:\s*(\d+)', cleaned)
        if sl_match: args["start_line"] = int(sl_match.group(1))
        el_match = re.search(r'"end_line"\s*:\s*(\d+)', cleaned)
        if el_match: args["end_line"] = int(el_match.group(1))
        snip_match = re.search(r'"expected_start_snippet"\s*:\s*"(.*?)"', cleaned)
        if snip_match:
            args["expected_start_snippet"] = snip_match.group(1).replace('\\"', '"').replace('\\\\', '\\')
        end_snip_match = re.search(r'"expected_end_snippet"\s*:\s*"(.*?)"', cleaned)
        if end_snip_match:
            args["expected_end_snippet"] = end_snip_match.group(1).replace('\\"', '"').replace('\\\\', '\\')

        content_match = re.search(r'"content"\s*:\s*"', cleaned)
        if content_match:
            start_idx = content_match.end()
            end_match = re.search(r'"\s*\}\s*\}\s*$', cleaned) or re.search(r'"\s*\}\s*$', cleaned)
            if end_match:
                raw_content_slice = cleaned[start_idx:end_match.start()]
            else:
                raw_tail = cleaned[start_idx:].rstrip(' \n\t}')
                if raw_tail.endswith('"'): raw_tail = raw_tail[:-1]
                raw_content_slice = raw_tail

            # raw_content_slice is still-encoded JSON string content (never passed
            # through json.loads). Decode it properly as a JSON string instead of
            # hand-rolled sequential replaces, which mishandle multi-level escaping.
            try:
                args["content"] = json.loads(f'"{raw_content_slice}"')
            except json.JSONDecodeError:
                import codecs
                # Safely decode the raw slice exactly as Python parses string escapes
                try:
                    args["content"] = codecs.decode(raw_content_slice, 'unicode_escape')
                except Exception:
                    args["content"] = raw_content_slice
            args["content"] = _normalize_double_escaped_content(args["content"])
            args["content"] = _clean_over_escaped_quotes(args["content"])
        elif raw_payload is not None:
            args["content"] = raw_payload
        else:
            args["content"] = ""

        if "filepath" in args and "start_line" in args and "end_line" in args:
            return {"name": tool_name, "args": args}

    elif tool_name == "patch_file":
        fp_match = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp_match:
            args["filepath"] = fp_match.group(1)

        oc_match = re.search(r'"old_content"\s*:\s*"', cleaned)
        if oc_match:
            start_oc = oc_match.end()
            end_oc_match = re.search(r'",\s*"new_content"', cleaned)
            if end_oc_match:
                args["old_content"] = cleaned[start_oc:end_oc_match.start()]
            else:
                args["old_content"] = cleaned[start_oc:].split('",')[0]
            args["old_content"] = args["old_content"].replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')

        nc_match = re.search(r'"new_content"\s*:\s*"', cleaned)
        if nc_match:
            start_nc = nc_match.end()
            end_nc_match = re.search(r'"\s*\}\s*\}\s*$', cleaned) or re.search(r'"\s*\}\s*$', cleaned)
            if end_nc_match:
                args["new_content"] = cleaned[start_nc:end_nc_match.start()]
            else:
                raw_tail = cleaned[start_nc:].rstrip(' \n\t}')
                if raw_tail.endswith('"'): raw_tail = raw_tail[:-1]
                args["new_content"] = raw_tail
            args["new_content"] = args["new_content"].replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')

        if "filepath" in args and "old_content" in args and "new_content" in args:
            return {"name": tool_name, "args": args}

    elif tool_name == "run_cmd":
        cmd_match = re.search(r'"command"\s*:\s*"', cleaned)
        if cmd_match:
            start_idx = cmd_match.end()
            end_match = re.search(r'"\s*\}\s*\}\s*$', cleaned) or re.search(r'"\s*\}\s*$', cleaned)
            if end_match:
                args["command"] = cleaned[start_idx:end_match.start()].replace('\\"', '"').replace('\\\\', '\\')
            else:
                raw_tail = cleaned[start_idx:].rstrip(' \n\t}')
                if raw_tail.endswith('"'): raw_tail = raw_tail[:-1]
                args["command"] = raw_tail.replace('\\"', '"').replace('\\\\', '\\')
            return {"name": tool_name, "args": args}

    elif tool_name == "read_file":
        fp_match = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp_match: args["filepath"] = fp_match.group(1)
        sl_match = re.search(r'"start_line"\s*:\s*(\d+)', cleaned)
        if sl_match: args["start_line"] = int(sl_match.group(1))
        ml_match = re.search(r'"max_lines"\s*:\s*(\d+)', cleaned)
        if ml_match: args["max_lines"] = int(ml_match.group(1))
        return {"name": tool_name, "args": args}

    elif tool_name == "list_tree":
        dp_match = re.search(r'"dir_path"\s*:\s*"(.*?)"', cleaned)
        if dp_match: args["dir_path"] = dp_match.group(1)
        md_match = re.search(r'"max_depth"\s*:\s*(\d+)', cleaned)
        if md_match: args["max_depth"] = int(md_match.group(1))
        return {"name": tool_name, "args": args}

    elif tool_name == "search_codebase":
        dp_match = re.search(r'"dir_path"\s*:\s*"(.*?)"', cleaned)
        if dp_match: args["dir_path"] = dp_match.group(1)
        q_match = re.search(r'"query"\s*:\s*"(.*?)"', cleaned)
        if q_match: args["query"] = q_match.group(1).replace('\\"', '"').replace('\\\\', '\\')
        re_match = re.search(r'"is_regex"\s*:\s*(true|false)', cleaned, re.IGNORECASE)
        if re_match: args["is_regex"] = re_match.group(1).lower() == 'true'
        mm_match = re.search(r'"max_matches"\s*:\s*(\d+)', cleaned)
        if mm_match: args["max_matches"] = int(mm_match.group(1))
        return {"name": tool_name, "args": args}

    elif tool_name == "extract_code_blocks":
        try:
            return {"name": tool_name, "args": json.loads(cleaned).get("args", {})}
        except json.JSONDecodeError:
            raise json.JSONDecodeError("Failed to parse extract_code_blocks arguments.", cleaned, 0)

    elif tool_name == "read_symbol":
        fp_match = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp_match:
            args["filepath"] = fp_match.group(1)

        sym_match = re.search(r'"symbol_name"\s*:\s*"(.*?)"', cleaned)
        if sym_match:
            args["symbol_name"] = sym_match.group(1).replace('\\"', '"').replace('\\\\', '\\')

        return {"name": tool_name, "args": args}

    raise json.JSONDecodeError("Fallback pattern parser extraction failed.", json_clean, 0)
