import os

from .tool_definitions import read_file


def build_hidden_readme_prompt(abs_target_dir, repo_tree, existing_readme, strategy_steps, code_summary=None, cli_help=None):
    """
    Scaffolding function for building a hidden README prompt with optional deep context.
    """
    deep_section = ""
    deep_guardrail = ""
    if code_summary and cli_help:
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
        f"CRITICAL: Do not call tools with empty arguments or empty payloads."
    )


def build_strategy_steps(readme_path, allow_patch, deep_focus=False):
    focus_rule = ""

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
            f"2. Use `write_file` along with the `<payload>` block to initialize the README file from scratch incorporating explicit usage documentation.\n"
            f"3. COMPLETION (CRITICAL): After the file is written, output a final conversational message announcing completion and DO NOT invoke any further tools."
            f"{focus_rule}"
        )
