import re
from coding_agent.tool_definitions import (
    read_file,
    write_file,
    append_file,
    replace_lines,
    patch_file,
    run_cmd,
    list_tree,
    search_codebase
)
from coding_agent import native_linter


def execute_tool(tool_name, tool_args, is_split_mode):
    """
    Routes an approved tool call to the correct underlying function.
    Returns a tuple of (tool_result, tool_reinforcement, file_was_modified).
    """
    tool_result = ""
    tool_reinforcement = ""
    file_was_modified = False

    if tool_name == "read_file":
        s_line = tool_args.get("start_line", 1)
        m_lines = tool_args.get("max_lines", 75)
        tool_result = read_file(tool_args.get("filepath"), start_line=s_line, max_lines=m_lines)

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

        # --- SMART AUTO-INDENTATION FIX ---
        if filepath and start_line is not None:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    file_lines = f.readlines()

                start_idx = start_line - 1
                if 0 <= start_idx < len(file_lines):
                    original_line = file_lines[start_idx]
                    # Extract the leading whitespace (spaces/tabs) from the target line
                    original_indent = original_line[:len(original_line) - len(original_line.lstrip(' \t'))]

                    new_lines = content.splitlines()
                    # If the LLM sent code flush-left, but the original file line was indented:
                    if original_indent and new_lines and not new_lines[0].startswith((' ', '\t')):
                        indented_lines = []
                        for line in new_lines:
                            if line.strip():  # Don't add spaces to empty lines
                                indented_lines.append(original_indent + line)
                            else:
                                indented_lines.append(line)
                        content = '\n'.join(indented_lines)
            except Exception:
                pass  # Fallback gracefully if any read error occurs
        # -----------------------------------

        tool_result = replace_lines(filepath, start_line, end_line, content)
        file_was_modified = tool_result.startswith("Successfully")

        linter_error = None
        if file_was_modified:
            # Run linter after line replacement to catch broken indentation or missing imports
            linter_error = native_linter.check_python_syntax_and_imports(filepath)
            if linter_error:
                tool_result += f"\n\n⚠️ CRITICAL WARNING: The lines were replaced, but the linter found an issue:\n{linter_error}\nPlease immediately fix this file by adding the missing imports or correcting the syntax."

        if is_split_mode:
            tool_reinforcement = "\n\n(System Rule: Line replacement successful. If your wiring is done, output 'Refactor Phase Complete'.)"
        else:
            tool_reinforcement = "\n\n(System Rule: Line replacement successful. Do not summarize. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

    elif tool_name == "run_cmd":
        tool_result = run_cmd(tool_args.get("command"))

    else:
        tool_result = "Error: Unknown tool."

    return tool_result, tool_reinforcement, file_was_modified
