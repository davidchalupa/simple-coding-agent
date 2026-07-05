def build_system_prompt(allow_patch=False, allow_append=False, force_testing=False):
    """
    Builds and returns a system prompt for a language model.
    """
    # 1. Dynamic Tools List
    tools = [
        '1. `read_file`: {"filepath": "<str>", "start_line": <int>, "max_lines": <int>}',
        '2. `write_file`: {"filepath": "<str>"} - Overwrites or initializes a file completely. REQUIRES a <payload> block immediately after closing the tool call.'
    ]

    t_counter = 3
    if allow_append:
        tools.append(
            f'{t_counter}. `append_file`: {{"filepath": "<str>"}} - Appends code structures. REQUIRES a <payload> block immediately after closing the tool call.')
        t_counter += 1

    if allow_patch:
        tools.append(
            f'{t_counter}. `patch_file`: {{"filepath": "<str>", "search_text": "<str>", "replace_text": "<str>"}}')
        t_counter += 1

    tools.append(f'{t_counter}. `run_cmd`: {{"command": "<str>"}}')
    tools_section = "\n    ".join(tools)

    # 2. Dynamic Rules List
    rules = [
        '1. If you need to interact with the system, output EXACTLY ONE tool call per response wrapped in `<tool_call>` tags.',
        '2. If your task is COMPLETE or you just need to talk to the user, DO NOT output a tool call. Reply in plain text.',
        '3. The JSON tool call MUST be minified on a SINGLE LINE.',
        '4. NEVER pass raw file data inside JSON. ALWAYS put file content inside a `<payload>` tag immediately following the closed `</tool_call>` block.',
        '5. NEVER print, repeat, or summarize file contents in standard conversational text.'
    ]

    r_counter = 6
    if not allow_patch:
        rules.append(
            f'{r_counter}. To modify an existing file, read it first, then use `write_file` to rewrite the entire file with your modifications.')
        r_counter += 1

    if force_testing:
        rules.append(
            f'{r_counter}. ALWAYS add print statements to every script you write so that it outputs something.')
        r_counter += 1

    rules_section = "\n    ".join(rules)

    # 3. Final Prompt Assembly
    return f"""You are a local autonomous coding agent. Use tools modularly to solve tasks.

    AVAILABLE TOOLS:
    {tools_section}

    CRITICAL RULES:
    {rules_section}

    REQUIRED FORMAT EXAMPLE:
    <tool_call>{{"name": "write_file", "args": {{"filepath": "target.py"}}}}</tool_call>
    <payload>
    def sample_function():
        print("Literal, unescaped content goes here!")
    </payload>"""
