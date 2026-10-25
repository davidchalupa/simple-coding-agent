import os

def format_tool_args_for_display(state, tool_args):
    """Convert tool args to a display-safe dictionary with relative paths."""
    if not isinstance(tool_args, dict):
        return {"args": tool_args}

    formatted = {}
    for key, value in tool_args.items():
        if key in ("filepath", "dir_path") and isinstance(value, str) and os.path.isabs(value):
            try:
                formatted[key] = os.path.relpath(value, state.session_cwd)
            except ValueError:  # Different drive on Windows
                formatted[key] = value
        else:
            formatted[key] = value
    return formatted
