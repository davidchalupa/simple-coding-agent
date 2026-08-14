import json
import re


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
        naked_match = re.search(r'\{\s*"name"\s*:\s*"[^"]+".*?\}', response_content, re.DOTALL)
        if naked_match:
            tool_json_str = naked_match.group(0).strip()
            search_start = naked_match.end()

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

    try:
        data = json.loads(json_clean, strict=False)
        if "name" not in data or data.get("name") is None:
            raise json.JSONDecodeError(
                "Parsed JSON has no 'name' field — likely matched the wrong block.",
                json_clean, 0
            )
        if "args" not in data:
            data["args"] = {}

        if raw_payload is not None:
            if data.get("name") in ["write_file", "append_file", "replace_lines"]:
                data["args"]["content"] = raw_payload
        else:
            if data.get("name") in ["write_file", "append_file", "replace_lines"] and "content" not in data["args"]:
                data["args"]["content"] = ""

        return data
    except json.JSONDecodeError:
        pass

    cleaned = json_clean.strip()

    allowed_tools = "write_file|append_file|replace_lines|read_file|run_cmd|patch_file|extract_code_blocks|list_tree|search_codebase" if allow_patch else "write_file|append_file|read_file|run_cmd|extract_code_blocks|list_tree|search_codebase"
    name_match = re.search(fr'"name"\s*:\s*"({allowed_tools})"', cleaned)

    if not name_match:
        raise json.JSONDecodeError("Could not isolate tool name signature from model string.", json_clean, 0)

    tool_name = name_match.group(1)
    args = {}

    if tool_name in ["write_file", "append_file"]:
        fp_match = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp_match:
            args["filepath"] = fp_match.group(1)

        if raw_payload is not None:
            args["content"] = raw_payload
        else:
            if '"name"' in cleaned or '"args"' in cleaned:
                raise json.JSONDecodeError(
                    "CRITICAL: You forgot to provide the raw file data! "
                    "You must include a separate <payload> block containing the actual code.",
                    cleaned, 0
                )

            content_match = re.search(r'"content"\s*:\s*"', cleaned)
            if content_match:
                start_idx = content_match.end()
                end_match = re.search(r'"\s*\}\s*\}\s*$', cleaned) or re.search(r'"\s*\}\s*$', cleaned)
                if end_match:
                    args["content"] = cleaned[start_idx:end_match.start()]
                else:
                    raw_tail = cleaned[start_idx:].rstrip(' \n\t}')
                    if raw_tail.endswith('"'): raw_tail = raw_tail[:-1]
                    args["content"] = raw_tail
                args["content"] = args["content"].replace('\\"', '"').replace('\\\\', '\\')
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

        if raw_payload is not None:
            args["content"] = raw_payload
        else:
            args["content"] = ""

        if "filepath" in args and "start_line" in args and "end_line" in args:
            return {"name": tool_name, "args": args}

    elif tool_name == "patch_file":
        fp_match = re.search(r'"filepath"\s*:\s*"(.*?)"', cleaned)
        if fp_match:
            args["filepath"] = fp_match.group(1)

        st_match = re.search(r'"search_text"\s*:\s*"', cleaned)
        if st_match:
            start_st = st_match.end()
            end_st_match = re.search(r'",\s*"replace_text"', cleaned)
            if end_st_match:
                args["search_text"] = cleaned[start_st:end_st_match.start()]
            else:
                args["search_text"] = cleaned[start_st:].split('",')[0]
            args["search_text"] = args["search_text"].replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')

        rt_match = re.search(r'"replace_text"\s*:\s*"', cleaned)
        if rt_match:
            start_rt = rt_match.end()
            end_rt_match = re.search(r'"\s*\}\s*\}\s*$', cleaned) or re.search(r'"\s*\}\s*$', cleaned)
            if end_rt_match:
                args["replace_text"] = cleaned[start_rt:end_rt_match.start()]
            else:
                raw_tail = cleaned[start_rt:].rstrip(' \n\t}')
                if raw_tail.endswith('"'): raw_tail = raw_tail[:-1]
                args["replace_text"] = raw_tail
            args["replace_text"] = args["replace_text"].replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')

        if "filepath" in args and "search_text" in args and "replace_text" in args:
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

    raise json.JSONDecodeError("Fallback pattern parser extraction failed.", json_clean, 0)
