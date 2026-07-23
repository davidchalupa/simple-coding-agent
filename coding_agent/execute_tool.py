import re
from coding_agent.tool_definitions import (
    read_file,
    write_file,
    append_file,
    patch_file,
    run_cmd
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
        tool_result = patch_file(tool_args.get("filepath"), tool_args.get("search_text"), tool_args.get("replace_text"))
        if is_split_mode:
            tool_reinforcement = "\n\n(System Rule: Patch successful. If your wiring is done, output 'Refactor Phase Complete'.)"
        else:
            tool_reinforcement = "\n\n(System Rule: Patch successful. Do not summarize. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

    elif tool_name == "run_cmd":
        tool_result = run_cmd(tool_args.get("command"))

    else:
        tool_result = "Error: Unknown tool."

    return tool_result, tool_reinforcement, file_was_modified
