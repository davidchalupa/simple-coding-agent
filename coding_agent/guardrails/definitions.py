READ_ONLY_TOOLS = {"read_file", "read_symbol", "search_codebase", "list_tree"}

TOOL_REQUIRED_FIELDS = {
    "read_file": {"filepath"},
    "read_symbol": {"filepath", "symbol_name"},
    "write_file": {"filepath", "content"},
    "append_file": {"filepath", "content"},
    "patch_file": {"filepath", "old_content", "new_content"},
    "replace_lines": {"filepath", "start_line", "end_line", "content"},
    "search_codebase": {"dir_path", "query"},
    "list_tree": {"dir_path"},
    "run_cmd": {"command"},
}

# Fields that belong to a DIFFERENT tool — presence signals the model picked the wrong tool
TOOL_FORBIDDEN_FIELDS = {
    "patch_file": {"start_line", "end_line"},
    "replace_lines": {"old_content", "new_content"},
}
