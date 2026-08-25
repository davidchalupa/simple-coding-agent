import os

from .tool_definitions import read_file


def build_hidden_readme_prompt(abs_target_dir, repo_tree, existing_readme, strategy_steps, code_summary=None, cli_help=None):
    """
    Scaffolding function for building a hidden README prompt with optional deep context.
    """
    deep_section = ""
    deep_guardrail = ""
    if code_summary and cli_help:
        # NOTE: code_summary already includes any real packaging/dependency metadata
        # (requirements.txt, pyproject.toml, etc.) prepended by gather_deep_context /
        # gather_deep_context_ast, so it doesn't need to be threaded separately here.
        deep_section = (
            f"--- REAL FILE CONTENTS (DEEP SCAN) ---\n{code_summary}\n--------------------------\n\n"
            f"--- RUNTIME CLI HELP OUTPUT ---\n{cli_help}\n--------------------------\n\n"
        )

        # --- CONDITIONAL GUARDRAIL FOR DEEP SCAN MODE ---
        # Prevents 7B models from getting distracted by raw code and slipping into code-completion mode
        deep_guardrail = (
            "\n\nCRITICAL AGENT INSTRUCTION:\n"
            "I have ALREADY extracted and summarized the repository code for you above. "
            "DO NOT write Python scripts to analyze the repository. "
            "DO NOT write Python code to simulate file reading. "
            "Your ONLY task is to draft the README content based on the text above, "
            "and output a valid `<tool_call>` using the `write_file` tool to save it to disk.\n\n"
        )

    installation_guardrail = (
        "\n\nINSTALLATION SECTION RULES (CRITICAL):\n"
        "- Do NOT invent a git remote URL (e.g. 'https://github.com/your-repo/...'). You do not know the real repository URL.\n"
        "- If a 'REAL FILE CONTENTS' section above includes a requirements.txt, pyproject.toml, setup.py, or setup.cfg, "
        "base your installation instructions on its ACTUAL contents.\n"
        "- If no packaging metadata is present, describe installation simply as: copy this directory and ensure the "
        "required Python version is installed. Do NOT fabricate a `git clone` command or package name.\n"
        "- Write each instruction line ONCE. Do not repeat similar lines multiple times."
    )

    return (
        f"The user wants to evaluate and maintain a clean, high-quality documentation README file.\n\n"
        f"--- CONTEXT ---\n"
        f"Target Directory: '{abs_target_dir}'\n"
        f"Target File: README.md (Use exactly this relative filename in your tool calls)\n\n"
        f"--- CURRENT REPOSITORY STRUCTURE ---\n{repo_tree}\n--------------------------\n\n"
        f"{deep_section}"
        f"--- ENTIRE EXISTING README CONTENT ---\n{existing_readme}\n--------------------------\n\n"
        f"STRATEGY:\n{strategy_steps}\n\n"
        f"{deep_guardrail}"
        f"{installation_guardrail}\n\n"
        f"CRITICAL: Do not call tools with empty arguments or empty payloads."
    )


def build_strategy_steps(readme_path, allow_patch, deep_focus=False):
    # Dynamically adjust the strict constraints based on what the model can actually see
    if deep_focus:
        focus_rule = (
            "\n\nSTRICT FORMATTING CONSTRAINTS:\n"
            "- AVOID GENERIC BOILERPLATE: Do NOT generate generic 'Contributing' or standard Git workflows unless explicitly found in the codebase.\n"
            "- GROUNDED CONTENT ONLY: Only document features, classes, functions, or CLI flags that are proven to exist in the provided file contents and CLI output.\n"
            "- REQUIRED STRUCTURE: Organize the README with clear headers: Project Overview, Core Architecture (based on actual files), and Usage."
        )
    else:
        focus_rule = (
            "\n\nSTRICT FORMATTING CONSTRAINTS:\n"
            "- AVOID GENERIC BOILERPLATE: Do NOT generate generic 'Contributing' or standard Git workflows.\n"
            "- NO CODE HALLUCINATION: You only have access to the file tree. DO NOT invent CLI commands, Python code blocks, or function names. Describe the project's high-level purpose based ONLY on the directory structure.\n"
            "- REQUIRED STRUCTURE: Organize the README with clear headers: Project Overview and Directory Architecture."
        )

    # Direct the model to pay attention to structural layout vs hard evidence
    grounding_source = "the repo structure, file contents, and runtime CLI usage parameters provided" if deep_focus else "the raw repository structure"

    if os.path.exists(readme_path):
        tool_choice = "`patch_file` or `write_file`" if allow_patch else "ONE `write_file`"
        return (
            f"1. ANALYSIS PHASE: Begin your response with a bulleted list comparing the files mentioned in the Existing README against {grounding_source}.\n"
            f"2. UPDATE PHASE: If discrepancies exist or specific usage instructions are missing, execute {tool_choice} tool call to fix the README.\n"
            f"3. COMPLETION (CRITICAL): Once your tool call executes successfully, or if no updates are needed, your task is complete. Output a short text confirmation and DO NOT invoke any further tools."
            f"{focus_rule}"
        )
    else:
        return (
            f"1. Evaluate {grounding_source} to extract the precise concept, underlying logic, and factual CLI execution methods of the project.\n"
            f"2. Use the `write_file` tool, embedding the full README content directly inside the JSON `content` field "
            f"(properly escaped, e.g. \\n for newlines), to initialize the README file from scratch incorporating "
            f"explicit usage documentation. Do NOT use a `<payload>` block.\n"
            f"3. COMPLETION (CRITICAL): After the file is written, output a final conversational message announcing completion and DO NOT invoke any further tools."
            f"{focus_rule}"
        )
