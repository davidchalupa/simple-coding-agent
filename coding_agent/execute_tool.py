import re
from coding_agent.tool_definitions import (
    read_file,
    write_file,
    append_file,
    replace_lines,
    patch_file,
    run_cmd,
    list_tree,
    search_codebase,
    read_symbol
)
from coding_agent import native_linter


def execute_tool_without_rollback(tool_name, tool_args, is_split_mode):
    """
    Routes an approved tool call to the correct underlying function.
    Returns a tuple of (tool_result, tool_reinforcement, file_was_modified).
    """
    # =========================================================================
    # TYPE ENFORCEMENT & LAZY JSON RECOVERY
    # =========================================================================
    if tool_args is None:
        tool_args = {}
    elif isinstance(tool_args, str):
        # If the LLM passes a raw string instead of a dictionary, try to salvage it
        if tool_name == "run_cmd":
            tool_args = {"command": tool_args}
        elif tool_name == "read_file":
            tool_args = {"filepath": tool_args}
        else:
            # If we can't safely guess how to map the string, return a graceful error
            # instead of crashing the entire test harness.
            error_msg = (
                f"System Error: Tool '{tool_name}' expects a JSON object for arguments, "
                f"but received a raw string: '{tool_args}'. Please use a valid JSON dictionary."
            )
            return error_msg, "", False
    # =========================================================================

    tool_result = ""
    tool_reinforcement = ""
    file_was_modified = False

    if tool_name == "read_file":
        s_line = tool_args.get("start_line", 1)
        m_lines = tool_args.get("max_lines", 75)
        tool_result = read_file(tool_args.get("filepath"), start_line=s_line, max_lines=m_lines)

    elif tool_name == "read_symbol":
        fp = tool_args.get("filepath")
        sym = tool_args.get("symbol_name")
        tool_result = read_symbol(fp, sym)

    elif tool_name == "list_tree":
        d_path = tool_args.get("dir_path", ".")
        m_depth = tool_args.get("max_depth", 2)
        tool_result = list_tree(dir_path=d_path, max_depth=m_depth)

    elif tool_name == "search_codebase":
        d_path = tool_args.get("dir_path", ".")
        qry = tool_args.get("query", "")
        is_re = tool_args.get("is_regex", False)
        m_matches = tool_args.get("max_matches", 50)
        tool_result = search_codebase(dir_path=d_path, query=qry, is_regex=is_re, max_matches=m_matches)

    elif tool_name == "write_file":
        file_was_modified = True
        content = tool_args.get("content", "")
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```$", "", content)

        tool_result = write_file(tool_args.get("filepath"), content)

        linter_error = native_linter.check_python_syntax_and_imports(tool_args.get("filepath"))
        if linter_error:
            tool_result += f"\n\n⚠️ CRITICAL WARNING: The file was written, but the linter found an issue:\n{linter_error}\nPlease immediately fix this file by adding the missing imports or correcting the syntax."

        if is_split_mode:
            tool_reinforcement = "\n\n(System Rule: Write successful. If your wiring is done, output 'Refactor Phase Complete'.)"
        else:
            tool_reinforcement = "\n\n(System Rule: Write successful. Do NOT output the file's contents. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

    elif tool_name == "append_file":
        file_was_modified = True
        content = tool_args.get("content", "")
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```$", "", content)

        tool_result = append_file(tool_args.get("filepath"), content)

        if is_split_mode:
            tool_reinforcement = "\n\n(System Rule: Append successful. If your wiring is done, output 'Refactor Phase Complete'.)"
        else:
            tool_reinforcement = "\n\n(System Rule: Append successful. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

    elif tool_name == "patch_file":
        file_was_modified = True
        filepath = tool_args.get("filepath")
        old_content = tool_args.get("old_content", "")
        new_content = tool_args.get("new_content", "")

        # Strip markdown fences if the LLM wraps the payloads in them
        old_content = re.sub(r"^```[a-zA-Z]*\n", "", old_content)
        old_content = re.sub(r"\n```$", "", old_content)
        new_content = re.sub(r"^```[a-zA-Z]*\n", "", new_content)
        new_content = re.sub(r"\n```$", "", new_content)

        tool_result = patch_file(filepath, old_content, new_content)

        file_was_modified = tool_result.startswith("Successfully")

        # Run linter after patching to catch broken indentation or missing imports
        linter_error = native_linter.check_python_syntax_and_imports(filepath)
        if linter_error:
            tool_result += f"\n\n⚠️ CRITICAL WARNING: The file was patched, but the linter found an issue:\n{linter_error}\nPlease immediately fix this file by adding the missing imports or correcting the syntax."

        if is_split_mode:
            tool_reinforcement = "\n\n(System Rule: Patch successful. If your wiring is done, output 'Refactor Phase Complete'.)"
        else:
            tool_reinforcement = "\n\n(System Rule: Patch successful. Do not summarize. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

    elif tool_name == "replace_lines":
        content = tool_args.get("content", "")

        # Strip markdown fences if the LLM wraps the payload in them
        content = re.sub(r"^```[a-zA-Z]*\n", "", content)
        content = re.sub(r"\n```$", "", content)

        filepath = tool_args.get("filepath")
        start_line = tool_args.get("start_line")
        end_line = tool_args.get("end_line")
        expected_start_snippet = tool_args.get("expected_start_snippet")
        expected_end_snippet = tool_args.get("expected_end_snippet")

        # --- SMART AUTO-INDENTATION FIX ---
        if filepath and start_line is not None:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    file_lines = f.readlines()

                start_idx = start_line - 1
                if 0 <= start_idx < len(file_lines):
                    original_line = file_lines[start_idx]
                    original_indent = original_line[:len(original_line) - len(original_line.lstrip(' \t'))]

                    new_lines = content.splitlines()
                    if new_lines:
                        # Find the first non-blank line's actual indent in the model's content
                        first_nonblank = next((l for l in new_lines if l.strip()), "")
                        content_indent = first_nonblank[:len(first_nonblank) - len(first_nonblank.lstrip(' \t'))]

                        if content_indent != original_indent:
                            # Re-base every line: strip the model's own leading indent, apply the correct one
                            rebased_lines = []
                            for line in new_lines:
                                if line.strip():
                                    stripped = line[len(content_indent):] if line.startswith(
                                        content_indent) else line.lstrip(' \t')
                                    rebased_lines.append(original_indent + stripped)
                                else:
                                    rebased_lines.append(line)
                            content = '\n'.join(rebased_lines)
            except Exception:
                pass
        # -----------------------------------

        file_was_modified, tool_result = replace_lines(filepath, start_line, end_line, content, expected_start_snippet,
                                    expected_end_snippet)

        linter_error = None
        if file_was_modified:
            linter_error = native_linter.check_python_syntax_and_imports(filepath)
            if linter_error:
                tool_result += f"\n\n⚠️ CRITICAL WARNING: The lines were replaced, but the linter found an issue:\n{linter_error}\nPlease immediately fix this file by adding the missing imports or correcting the syntax."

        if not file_was_modified:
            tool_reinforcement = (
                "\n\n(System Rule: Line replacement FAILED. The error message above tells you the exact "
                "corrected line number(s) to use, or explains what expected_end_snippet should actually be "
                "(the signature of the NEXT function, not the last line of this one). Retry accordingly, "
                "keeping the same content unless the anchors themselves were wrong.)"
            )
        elif is_split_mode:
            tool_reinforcement = "\n\n(System Rule: Line replacement successful. If your wiring is done, output 'Refactor Phase Complete'.)"
        else:
            tool_reinforcement = "\n\n(System Rule: Line replacement successful. Do not summarize. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

    elif tool_name == "run_cmd":
        tool_result = run_cmd(tool_args.get("command"))

    else:
        tool_result = "Error: Unknown tool."

    return tool_result, tool_reinforcement, file_was_modified


def execute_tool(tool_name, tool_args, is_split_mode):
    filepath = tool_args.get("filepath") if isinstance(tool_args, dict) else None
    original_content = None

    # Backup file contents before modifying disk
    if filepath and tool_name in ("write_file", "append_file", "patch_file", "replace_lines"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                original_content = f.read()
        except FileNotFoundError:
            original_content = None

    # Execute the underlying tool logic
    tool_result, tool_reinforcement, file_was_modified = execute_tool_without_rollback(tool_name, tool_args,
                                                                                       is_split_mode)

    # Validate syntax and roll back if corrupted
    if file_was_modified and filepath:
        linter_error = native_linter.check_python_syntax_and_imports(filepath)

        # If the linter returns an error and it's a severe syntax break
        if linter_error and "SyntaxError" in linter_error:
            if original_content is not None:
                # Rollback to original content
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(original_content)

                # OVERWRITE the tool_result and reinforcement completely
                tool_result = (
                    f"⚠️ ACTION REVERTED: The edit was applied, but introduced a severe syntax error:\n{linter_error}\n"
                    "The file has been restored to its previous state. Please correct your code formatting "
                    "(e.g., unescaped newlines like '\\n' inside strings), and try again."
                )
                # 🔥 CRITICAL FIX: Tell the agent it failed and must retry
                tool_reinforcement = "\n\n(System Rule: Your last action FAILED and was reverted due to a SyntaxError. You MUST fix the syntax and submit a corrected tool call. DO NOT output 'Task Complete'.)"
                file_was_modified = False
            else:
                tool_result += f"\n\n⚠️ CRITICAL WARNING: File contains syntax error:\n{linter_error}"

    return tool_result, tool_reinforcement, file_was_modified


# def execute_tool(tool_name, tool_args, is_split_mode):
#     return execute_tool_without_rollback(tool_name, tool_args, is_split_mode)
