import sys
import os
import shutil

from common.tool_definitions import read_file

from coding_agent.native_helpers import (get_repo_structure, generate_requirements_native, gather_deep_context,
                                         gather_deep_context_ast)

from coding_agent.guardrails.tools import verify_sandbox_health
from coding_agent import hidden_readme_prompt_builder
from coding_agent import split_tools


def handle_user_input(state, user_input, system_prompt):
    if user_input == "/quit":
        print("Exiting. Goodbye!")
        sys.exit(0)

    if user_input == "/clear":
        state.messages = [{"role": "system", "content": system_prompt}]
        state.session_cwd = os.getcwd()
        state.consult_read_cache = {}
        print("🧹 Memory and environment completely cleared!")
        return True

    if user_input == "/cancel":
        print("❌ Current draft discarded.")
        return True

    if not user_input:
        return True

    return False


def handle_macros(user_input, state, execution_state, system_prompt, context_window):
    """
    Handles the more complex macros. Returns True if a relevant macro was found but an error occurred
    and False if everything went right or no relevant macro was found.
    """
    # --- MACRO: /requirements ---
    if user_input.startswith("/requirements"):
        no_version_flag = "--no-version" in user_input
        cleaned_input = user_input.replace("--no-version", "").strip()
        parts = cleaned_input.split(" ", 1)
        target_dir = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "."

        abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
        state.session_cwd = abs_target_dir

        if not os.path.isdir(abs_target_dir):
            print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
            return True

        print(f"\n⚠️  MANUAL OVERRIDE: Generate requirements.txt natively? (No versions: {no_version_flag})")
        approval = input("Allow this action? (y/n): ").strip().lower()

        if approval == 'y':
            tool_result = generate_requirements_native(abs_target_dir, no_version=no_version_flag)
            state.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",
                 "content": f"System Alert: User manually ran /requirements for '{abs_target_dir}'. Result: {tool_result}. Briefly acknowledge completion."}
            ]
        else:
            print("🛑 Action blocked.")
            return True

        return False

    # --- MACRO: /readme ---
    elif user_input.startswith("/readme"):
        # 1. Split input into whole-word tokens to avoid substring match bugs
        tokens = user_input.split()

        deep_focus = "--deep" in tokens or "-d" in tokens
        deep_ast_focus = "--deep-ast" in tokens

        # 2. Filter out the command and the flags
        flags_to_remove = {"/readme", "--deep", "-d", "--deep-ast"}
        path_tokens = [t for t in tokens if t not in flags_to_remove]

        # 3. Join the remaining tokens to form the path (handles unquoted paths with spaces)
        target_dir = " ".join(path_tokens) if path_tokens else "."

        abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
        state.session_cwd = abs_target_dir

        if not os.path.isdir(abs_target_dir):
            print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
            return True

        print(f"\n🔍 Pre-computing repository structure for {abs_target_dir}...")

        repo_tree = get_repo_structure(abs_target_dir)
        readme_path = os.path.join(abs_target_dir, "README.md")

        if os.path.exists(readme_path):
            existing_readme = read_file(readme_path, start_line=1, max_lines=1000)
            print("   [Notice] Existing README.md found. Forcing structural analysis.")
        else:
            existing_readme = "STATUS: No README.md exists yet. DO NOT call read_file. You must create it from scratch using write_file."
            print("   [Notice] No README.md found. Agent will draft a new one.")

        # Deep Mode Trigger Interceptor
        code_summary = None
        cli_help = None
        if deep_ast_focus:
            print(
                "👀 [Mode Change] Experimental Dispatcher: Extracting AST interfaces and auto-routing based on size...")
            code_summary, cli_help = gather_deep_context_ast(abs_target_dir)
        elif deep_focus:
            print("👀 [Mode Change] Deep Scan: Extracting script code segments and querying CLI help hooks...")
            code_summary, cli_help = gather_deep_context(abs_target_dir, context_window)

        strategy_steps = hidden_readme_prompt_builder.build_strategy_steps(
            readme_path, state.allow_patch,
            deep_focus=(deep_focus or deep_ast_focus)
        )

        hidden_readme_prompt = hidden_readme_prompt_builder.build_hidden_readme_prompt(
            abs_target_dir, repo_tree, existing_readme, strategy_steps, code_summary=code_summary,
            cli_help=cli_help
        )

        state.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": hidden_readme_prompt}
        ]

        return False

    # --- MACRO: /split ---
    elif user_input.startswith("/split"):
        execute_mode = "--execute" in user_input
        cleaned_args = user_input.replace("--execute", "").strip().split(" ", 1)

        if len(cleaned_args) < 2 or not cleaned_args[1].strip():
            print("❌ Error: You must provide a filepath. Usage: /split [--execute] [filepath]")
            return True

        target_file = cleaned_args[1].strip()
        abs_target_file = os.path.abspath(os.path.expanduser(target_file))

        if not os.path.isfile(abs_target_file):
            print(f"❌ Error: Target file '{abs_target_file}' does not exist.")
            return True

        if execute_mode:
            print(
                f"\n⚠️  [WARNING] Execution Mode Active: System will use AST natively based on LLM JSON mapping.")
            print("   This isolates the agent from hallucinating logic blocks.")
            print(f"🔍 Initializing Sandbox and Parsing AST structure for {abs_target_file}...")
        else:
            print(f"\n🔍 Initializing Sandbox (Advisor Mode) for {abs_target_file}...")

        # 1. Setup sandbox tracking
        _, execution_state.sandbox_directory = split_tools.setup_refactor_sandbox(abs_target_file)
        execution_state.original_split_file = abs_target_file
        execution_state.is_split_mode = True
        execution_state.is_execute_mode = execute_mode

        # 2. Divert agent's current working directory to the sandbox!
        state.session_cwd = execution_state.sandbox_directory

        # Pass the flag to the prompt builder
        split_prompt = split_tools.build_split_prompt(abs_target_file, state.session_cwd, execute_mode=execute_mode)

        if not execute_mode:
            split_prompt += "\n\nFormat your plan now. Do not write file contents yet. Wait for confirmation."

        state.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": split_prompt}
        ]

        return False

    return False  # Return False if no macro was handled


def handle_ast_extraction_interception(state, execution_state, response_content):
    if "```json" in response_content:
        if execution_state.is_execute_mode:
            handled, alert = split_tools.handle_ast_extraction(response_content,
                                                               execution_state.original_split_file,
                                                               execution_state.sandbox_directory)
        else:
            print("\n📋 Advisor blueprint received:")
            approval = input(
                "Apply this blueprint deterministically to the sandbox? (y/n): ").strip().lower()
            if approval == 'y':
                handled, alert = split_tools.handle_ast_extraction(response_content,
                                                                   execution_state.original_split_file,
                                                                   execution_state.sandbox_directory)
            else:
                handled, alert = True, (
                    "Blueprint held pending revision. If you'd like changes, "
                    "explain them and provide an updated ```json blueprint."
                )
        if handled:
            state.messages.append({"role": "user", "content": alert})
            return True
    return False


def handle_sandbox_guardrail(execution_state, state, response_content):
    """SANDBOX GUARDRAIL: Only runs if NO tool was called."""
    if any(x in response_content.lower() for x in ["refactor phase complete", "task complete"]):
        passed, report = verify_sandbox_health(execution_state.original_split_file,
                                               execution_state.sandbox_directory, state.messages)
        if passed:
            print(f"✅ Sandbox passed! Staged in: {execution_state.sandbox_directory}")
            if execution_state.is_execute_mode:
                # Promotion done only in execute mode, just for safety
                if input("Promote to production? (y/n): ").strip().lower() == 'y':
                    target_dir = os.path.dirname(execution_state.original_split_file)
                    for item in os.listdir(execution_state.sandbox_directory):
                        if not item.startswith('.'):
                            shutil.copy2(os.path.join(execution_state.sandbox_directory, item),
                                         os.path.join(target_dir, item))
                    print("🚀 Files successfully promoted.")
            execution_state.is_split_mode = execution_state.is_execute_mode = False
            state.session_cwd = os.path.dirname(execution_state.original_split_file)
            return True
        print(f"❌ Verification Failed:\n{report}")
        state.messages.append({"role": "user",
                               "content": f"System Verification Failed:\n{report}\n\nCorrect this error and output 'Refactor Phase Complete'."})
        return False
    return False
