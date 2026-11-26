import os


def build_hidden_readme_prompt(abs_target_dir, repo_tree, existing_readme, strategy_steps, code_summary=None, cli_help=None):
    """
    Builds a grounded README prompt from repository structure, manifests,
    extracted code/AST context, and runtime CLI help output.
    """
    workspace_label = os.path.basename(abs_target_dir.rstrip("/\\")) or abs_target_dir

    deep_section = ""
    deep_guardrail = ""
    if code_summary or cli_help:
        deep_section = (
            f"--- EXTRACTED REPOSITORY & INTERFACE CONTEXT ---\n{code_summary or ''}\n--------------------------\n\n"
            f"--- RUNTIME CLI HELP OUTPUT ---\n{cli_help or 'None captured.'}\n--------------------------\n\n"
        )

        deep_guardrail = (
            "\n\nCRITICAL AGENT INSTRUCTION:\n"
            "I have ALREADY gathered repository manifests, code/AST context, and interface details above. "
            "DO NOT write scripts to scan or analyze the repository. "
            "DO NOT write code to simulate file reading. "
            "Your ONLY task is to draft the README content grounded strictly in the provided context, "
            "and output a valid `<tool_call>` using the `write_file` tool to save it to disk.\n\n"
        )

    installation_guardrail = (
        "\n\nINSTALLATION & SETUP RULES (CRITICAL):\n"
        "- Do NOT invent a git remote URL (e.g. 'https://github.com/your-repo/...'), clone commands, or temporary /tmp paths.\n"
        "- Do NOT invent virtual environment creation or activation steps (such as `venv`, `source .../activate`, "
        "`Scripts\\activate`, `conda`, or OS-specific shell commands) unless explicitly shown in the provided context.\n"
        "- Base installation and dependency notes ONLY on actual manifest/packaging files shown in the context "
        "(such as requirements.txt, pyproject.toml, setup.py, setup.cfg, package.json, Cargo.toml, or go.mod).\n"
        "- If no packaging manifest is present, state only that the files are used directly with the relevant language "
        "runtime, without fabricating install commands or package names.\n"
        "- Write each instruction line ONCE. Do not repeat similar lines multiple times."
    )

    return (
        f"The user wants to evaluate and maintain a clean, high-quality documentation README file.\n\n"
        f"--- CONTEXT ---\n"
        f"Project Workspace: '{workspace_label}'\n"
        f"Target File: README.md (Use exactly this relative filename in your tool calls)\n\n"
        f"--- CURRENT REPOSITORY STRUCTURE ---\n{repo_tree}\n--------------------------\n\n"
        f"{deep_section}"
        f"--- ENTIRE EXISTING README CONTENT ---\n{existing_readme}\n--------------------------\n\n"
        f"STRATEGY:\n{strategy_steps}\n\n"
        f"{deep_guardrail}"
        f"{installation_guardrail}\n\n"
        f"CRITICAL: Do not call tools with empty arguments or empty payloads.\n"
        f"You MUST enclose your tool call in a strict markdown block that explicitly starts with ```json and ends with ```."
    )


def build_strategy_steps(readme_path, allow_patch):
    focus_rule = (
        "\n\nSTRICT FORMATTING CONSTRAINTS:\n"
        "- AVOID GENERIC BOILERPLATE: Do NOT generate generic 'Contributing', standard Git workflows, "
        "boilerplate setup/activation instructions, or `<angle_bracket>` placeholders.\n"
        "- EXACT SYMBOLS & FILES ONLY: Every file name, module, class, function, CLI flag, or command you mention "
        "MUST appear verbatim in the provided context or CLI output. Never guess or infer filenames.\n"
        "- DENSE, FACTUAL COVERAGE: In Core Architecture and Usage, cite the actual files, classes/functions, "
        "entrypoints, commands, and flags present in the context without inventing shell commands or environment setup.\n"
        "- REQUIRED STRUCTURE: Organize the README with clear headers: Project Overview, Core Architecture "
        "(based on actual files and subsystems), and Usage."
    )

    grounding_source = "the repository structure, manifests, extracted code/AST context, and CLI help output provided"

    if os.path.exists(readme_path):
        tool_choice = "`patch_file` or `write_file`" if allow_patch else "ONE `write_file`"
        return (
            f"1. ANALYSIS PHASE: Begin your response with a bulleted list comparing the files and features mentioned in the Existing README against {grounding_source}.\n"
            f"2. UPDATE PHASE: If discrepancies exist or accurate usage instructions are missing, execute a {tool_choice} tool call to update the README.\n"
            f"3. COMPLETION (CRITICAL): Once your tool call executes successfully, or if no updates are needed, your task is complete. Output a short text confirmation and DO NOT invoke any further tools."
            f"{focus_rule}"
        )

    return (
        f"1. Evaluate {grounding_source} to extract the project's purpose, architecture, and factual usage/CLI details. "
        f"NOTE: README.md does not exist yet. DO NOT call read_file on it.\n"
        f"2. Use the `write_file` tool directly, embedding the full README content inside the JSON `content` field "
        f"(properly escaped, e.g. \\n for newlines), to create README.md from scratch. Do NOT use a `<payload>` block.\n"
        f"3. COMPLETION (CRITICAL): After the file is written, output a final conversational message announcing completion and DO NOT invoke any further tools."
        f"{focus_rule}"
    )
