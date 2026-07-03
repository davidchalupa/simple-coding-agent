import sys
import os
import json
import re
import shutil
from llama_cpp import Llama

from coding_agent.tool_definitions import read_file, write_file, append_file, patch_file, run_cmd
from coding_agent.system_prompt_builder import build_system_prompt
from coding_agent.native_helpers import get_repo_structure, generate_requirements_native
from coding_agent import hidden_readme_prompt_builder
from coding_agent import file_splitter
from coding_agent import native_linter
from coding_agent import payload_parser

# 1. Configuration
QWEN_PATH = "models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
CONTEXT_WINDOW = 8192  # Ensure maximum headroom for analysis
target_path = QWEN_PATH
loaded_model_name = "Qwen 2.5 Coder 7B (Agent Mode V11 Payload-Safe)"

ALLOW_PATCH = "--allow-patch" in sys.argv
FORCE_TESTING = "--force-testing" in sys.argv


# 4. Initialization Logic
if os.path.exists(target_path):
    print(f"Loading {loaded_model_name} into RAM...")
    try:
        llm = Llama(
            model_path=target_path, n_ctx=CONTEXT_WINDOW, n_threads=6, n_batch=512, verbose=False
        )
    except Exception as e:
        print(f"❌ Failed to load: {e}")
        sys.exit(1)
else:
    print(f"❌ Error: Model file not found at {target_path}.")
    sys.exit(1)

# 5. System Prompt & State Tracking
SYSTEM_PROMPT = build_system_prompt(ALLOW_PATCH)
messages = [{"role": "system", "content": SYSTEM_PROMPT}]
session_cwd = os.getcwd()

# --- SANDBOX STATE TRACKING ---
is_split_mode = False
original_split_file = None
sandbox_directory = None

print("\n" + "═" * 60)
print(f"🤖 Local Agent Initialized: [{loaded_model_name}]")
if ALLOW_PATCH:
    print("🔧 [STATUS] Patching Enabled")

print("\n🚀 Available Modes & Macros:")
print("  /requirements [--no-version] [path]")
print("      -> Natively generate requirements.txt")
print("\n  /readme [--conceptual] [path]")
print("      -> AI-driven repo documentation")
print("\n  /split [--execute] [filepath]")
print("      -> Refactor monoliths (Advisor mode or Logic Extraction mode)")
print("         [--execute] adds risk: agent will attempt full code refactoring")

print("\n⌨️  Commands:")
print("  /send  -> Submit your prompt")
print("  /clear -> Wipe conversation memory & reset environment")
print("  /quit  -> Terminate agent")
print("═" * 60)

# 6. Main Agent Loop
automated_followup = None  # Buffer for system-generated prompt injections

while True:
    user_input = ""

    # Check if we have an automated follow-up prompt queued
    if automated_followup:
        user_input = automated_followup
        automated_followup = None
        print(f"\n[Automated User]: {user_input}")
    else:
        # Standard human input loop
        print("\n[You] (Type /send to submit, /cancel to scratch draft, /undo to delete last line):")
        user_lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break

            trimmed = line.strip()

            if trimmed == "/send":
                break
            if trimmed == "/quit":
                print("Exiting. Goodbye!")
                sys.exit(0)

            if trimmed == "/clear":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                session_cwd = os.getcwd()
                is_split_mode = False
                original_split_file = None
                sandbox_directory = None
                automated_followup = None
                user_lines = []
                print("🧹 Memory and current draft completely cleared!")
                break

            if trimmed == "/cancel":
                user_lines = []
                print("❌ Current draft discarded. Start typing your new prompt below:")
                break

            if trimmed == "/undo":
                if user_lines:
                    removed = user_lines.pop()
                    print(f"🗑️  Removed line: \"{removed}\"")
                    print(f"Current buffer status ({len(user_lines)} lines active). Continue typing...")
                else:
                    print("⚠️ Buffer is already empty.")
                continue

            user_lines.append(line)

        # If a macro like /clear broke the inner loop, don't build user_input
        user_input = "\n".join(user_lines).strip()

    # Restart loop if no input was gathered
    if not user_input:
        continue

    # --- MACRO: /requirements ---
    if user_input.startswith("/requirements"):
        no_version_flag = "--no-version" in user_input
        cleaned_input = user_input.replace("--no-version", "").strip()
        parts = cleaned_input.split(" ", 1)
        target_dir = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "."

        abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
        session_cwd = abs_target_dir

        if not os.path.isdir(abs_target_dir):
            print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
            continue

        print(f"\n⚠️  MANUAL OVERRIDE: Generate requirements.txt natively? (No versions: {no_version_flag})")
        approval = input("Allow this action? (y/n): ").strip().lower()

        if approval == 'y':
            tool_result = generate_requirements_native(abs_target_dir, no_version=no_version_flag)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",
                 "content": f"System Alert: User manually ran /requirements for '{abs_target_dir}'. Result: {tool_result}. Briefly acknowledge completion."}
            ]
        else:
            print("🛑 Action blocked.")
            continue

    # --- MACRO: /readme ---
    elif user_input.startswith("/readme"):
        conceptual_focus = "--conceptual" in user_input or "-c" in user_input
        cleaned_args = user_input.replace("--conceptual", "").replace("-c", "").split(" ", 1)
        target_dir = cleaned_args[1].strip() if len(cleaned_args) > 1 and cleaned_args[1].strip() else "."

        abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
        session_cwd = abs_target_dir

        if not os.path.isdir(abs_target_dir):
            print(f"❌ Error: Target directory '{abs_target_dir}' does not exist.")
            continue

        print(f"\n🔍 Pre-computing repository structure for {abs_target_dir}...")

        repo_tree = get_repo_structure(abs_target_dir)
        readme_path = os.path.join(abs_target_dir, "README.md")

        if os.path.exists(readme_path):
            existing_readme = read_file(readme_path, start_line=1, max_lines=1000)
            print("   [Notice] Existing README.md found. Forcing structural analysis.")
        else:
            existing_readme = "No existing README.md found. Create from scratch."
            print("   [Notice] No README.md found. Agent will draft a new one.")

        if conceptual_focus:
            print("🧠 [Mode Change] Conceptual Focus: Focusing on project concept.")

        strategy_steps = hidden_readme_prompt_builder.build_strategy_steps(
            readme_path, ALLOW_PATCH, conceptual_focus=conceptual_focus
        )

        hidden_readme_prompt = hidden_readme_prompt_builder.build_hidden_readme_prompt(
            abs_target_dir, repo_tree, existing_readme, strategy_steps
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": hidden_readme_prompt}
        ]

    # --- MACRO: /split ---
    elif user_input.startswith("/split"):
        execute_mode = "--execute" in user_input
        cleaned_args = user_input.replace("--execute", "").strip().split(" ", 1)

        if len(cleaned_args) < 2 or not cleaned_args[1].strip():
            print("❌ Error: You must provide a filepath. Usage: /split [--execute] [filepath]")
            continue

        target_file = cleaned_args[1].strip()
        abs_target_file = os.path.abspath(os.path.expanduser(target_file))

        if not os.path.isfile(abs_target_file):
            print(f"❌ Error: Target file '{abs_target_file}' does not exist.")
            continue

        if execute_mode:
            print(
                f"\n⚠️  [WARNING] Execution Mode Active: The agent will attempt to migrate the full implementation logic.")
            print("   This is recommended ONLY for smaller files / simpler refactors.")
            print(f"🔍 Initializing Sandbox and Parsing AST structure for {abs_target_file}...")
        else:
            print(f"\n🔍 Initializing Sandbox (Advisor Mode) for {abs_target_file}...")

        # 1. Setup sandbox tracking
        _, sandbox_directory = file_splitter.setup_refactor_sandbox(abs_target_file)
        original_split_file = abs_target_file
        is_split_mode = True

        # 2. Divert agent's current working directory to the sandbox!
        session_cwd = sandbox_directory

        # Pass the flag to the prompt builder
        split_prompt = file_splitter.build_split_prompt(abs_target_file, session_cwd, execute_mode=execute_mode)

        split_prompt += "\n\nFormat your plan now. Do not write file contents yet. Wait for confirmation."

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": split_prompt}
        ]

    else:
        # Standard execution or continuation of sandbox mode
        messages.append({"role": "user", "content": user_input})

    # Internal Agent Execution Loop
    file_was_modified = False  # Track if any files change during this cycle

    while True:
        print(f"\n[Agent]: ", end="", flush=True)
        response_content = ""

        try:
            stream = llm.create_chat_completion(
                messages=messages, stream=True, temperature=0.1,
            )

            finish_reason = None
            for chunk in stream:
                choice = chunk['choices'][0]
                if choice.get('finish_reason'):
                    finish_reason = choice['finish_reason']

                delta = choice.get('delta')
                if 'content' in delta:
                    piece = delta['content']
                    print(piece, end="", flush=True)
                    response_content += piece

            is_truncated = (finish_reason == "length")

            if "<tool_call>" in response_content and "</tool_call>" not in response_content:
                response_content += "</tool_call>"
                print("</tool_call>", end="", flush=True)

            print()
            messages.append({"role": "assistant", "content": response_content})

            # --- SYSTEM GUARDRAIL INTERCEPTOR FOR SANDBOX MODE ---
            if is_split_mode and (
                    "refactor phase complete" in response_content.lower() or "task complete" in response_content.lower()):
                print("\n⚙️  [System Guardrail] Analyzing sandbox refactoring health...")

                # Step 1: Check structural integrity
                passed, report = file_splitter.verify_refactor_integrity(original_split_file, sandbox_directory)

                # Step 2: Native Linter Check
                if passed:
                    for root, _, files in os.walk(sandbox_directory):
                        for file in files:
                            if file.endswith('.py') and not file.startswith('.'):
                                filepath = os.path.join(root, file)
                                linter_error = native_linter.check_python_syntax_and_imports(filepath)

                                if linter_error:
                                    passed = False
                                    report = (
                                        f"Dependency Error in '{file}':\n{linter_error}\n"
                                        "Use your patch_file or write_file tool to add the missing imports at the top of the file."
                                    )
                                    break
                        if not passed:
                            break

                # Step 3: Resolution
                if passed:
                    print("✅ Sandbox passed structural AND dependency checks!")
                    print(f"Files are safely staged in: {sandbox_directory}")
                    approval = input("Would you like to promote these files to production? (y/n): ").strip().lower()

                    if approval == 'y':
                        target_dir = os.path.dirname(original_split_file)
                        for item in os.listdir(sandbox_directory):
                            src = os.path.join(sandbox_directory, item)
                            dst = os.path.join(target_dir, item)
                            if os.path.isfile(src) and not item.startswith('.'):
                                shutil.copy2(src, dst)
                        print("🚀 Files successfully promoted to production folder.")

                    # Reset Mode
                    is_split_mode = False
                    session_cwd = os.path.dirname(original_split_file)
                    break
                else:
                    print(f"❌ Verification Failed:\n{report}")
                    messages.append({
                        "role": "user",
                        "content": f"System Verification Failed:\n{report}\n\nPlease use your tools to correct this error. When done, output 'Refactor Phase Complete'."
                    })
                    continue
            # -----------------------------------------------------

            # Extract tool arguments
            tool_json_str = None
            tool_match = re.search(r"<tool_call>(.*?)</tool_call>", response_content, re.DOTALL)
            if tool_match:
                tool_json_str = tool_match.group(1).strip()
            else:
                for md_match in re.finditer(r"```json\s*\n(.*?)\n```", response_content, re.DOTALL):
                    candidate = md_match.group(1).strip()
                    if '"name"' in candidate:
                        tool_json_str = candidate
                        break

            if tool_json_str:
                try:
                    if is_truncated and "write_file" in response_content:
                        raise json.JSONDecodeError("Incomplete payload due to context limit truncation.", tool_json_str,
                                                   0)

                    tool_request = payload_parser.parse_robust_tool_call(response_content, tool_json_str)
                    tool_name = tool_request.get("name")
                    tool_args = tool_request.get("args", {})

                    if "filepath" in tool_args and not os.path.isabs(tool_args["filepath"]):
                        tool_args["filepath"] = os.path.abspath(os.path.join(session_cwd, tool_args["filepath"]))

                    if tool_name in ["write_file", "append_file"]:
                        content = tool_args.get('content', '')
                        clean_content = re.sub(r'```[a-zA-Z]*\s*```', '', content).strip()

                        if not clean_content:
                            print(f"🛑 [Parser Interceptor] Blocked an empty {tool_name} operation.")
                            messages.append({
                                "role": "user",
                                "content": f"System Alert: You attempted to call {tool_name} with an empty payload. If you have no changes to make or the task is complete, DO NOT output a <tool_call>. Announce completion in plain text instead."
                            })
                            continue

                    print(f"\n⚠️  AGENT REQUESTS PERMISSION TO EXECUTE: {tool_name}")
                    if tool_name in ["write_file", "append_file"]:
                        print(f"Resolved Target File: {tool_args.get('filepath')}")
                        print("Content Snippet: \n" + "-" * 20)
                        print(tool_args.get('content', '')[:300] + "\n...[truncated snippet]\n" + "-" * 20)
                    elif tool_name == "patch_file":
                        print(f"Patching File: {tool_args.get('filepath')}")
                        print(f"Targeting Code block:\n--->\n{tool_args.get('search_text')}\n<---")
                        print(f"Replacing With:\n--->\n{tool_args.get('replace_text')}\n<---")
                    else:
                        print(f"Arguments: {tool_args}")

                    approval = input("Allow this action? (y/n/edit): ").strip().lower()

                    tool_result = ""
                    tool_reinforcement = ""

                    if approval == 'y':
                        if tool_name == "read_file":
                            s_line = tool_args.get("start_line", 1)
                            m_lines = tool_args.get("max_lines", 75)
                            tool_result = read_file(tool_args.get("filepath"), start_line=s_line, max_lines=m_lines)

                        elif tool_name == "write_file":
                            file_was_modified = True  # Flag code modification
                            content = tool_args.get("content", "")
                            content = re.sub(r"^```[a-zA-Z]*\n", "", content)
                            content = re.sub(r"\n```$", "", content)

                            tool_result = write_file(tool_args.get("filepath"), content)

                            linter_error = native_linter.check_python_syntax_and_imports(tool_args.get("filepath"))
                            if linter_error:
                                tool_result += f"\n\n⚠️ CRITICAL WARNING: The file was written, but the linter found an issue:\n{linter_error}\nPlease immediately fix this file by adding the missing imports or correcting the syntax."

                            if is_split_mode:
                                tool_reinforcement = "\n\n(System Rule: Write successful. Continue splitting code into files. If done, output 'Refactor Phase Complete'.)"
                            else:
                                tool_reinforcement = "\n\n(System Rule: Write successful. Do NOT output the file's contents. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

                        elif tool_name == "append_file":
                            file_was_modified = True  # Flag code modification
                            content = tool_args.get("content", "")
                            content = re.sub(r"^```[a-zA-Z]*\n", "", content)
                            content = re.sub(r"\n```$", "", content)

                            tool_result = append_file(tool_args.get("filepath"), content)
                            if is_split_mode:
                                tool_reinforcement = "\n\n(System Rule: Append successful. Continue your task. If done, output 'Refactor Phase Complete'.)"
                            else:
                                tool_reinforcement = "\n\n(System Rule: Append successful. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

                        elif tool_name == "patch_file":
                            file_was_modified = True  # Flag code modification
                            tool_result = patch_file(tool_args.get("filepath"), tool_args.get("search_text"),
                                                     tool_args.get("replace_text"))
                            if is_split_mode:
                                tool_reinforcement = "\n\n(System Rule: Patch successful. Continue your task. If done, output 'Refactor Phase Complete'.)"
                            else:
                                tool_reinforcement = "\n\n(System Rule: Patch successful. Do not summarize. If your primary task is complete, state 'Task Complete' in plain text and STOP calling tools. Wait for the user.)"

                        elif tool_name == "run_cmd":
                            file_was_modified = False  # Reset flag upon test/execution
                            tool_result = run_cmd(tool_args.get("command"))
                        else:
                            tool_result = "Error: Unknown tool."
                        print(f"⚙️  Tool execution finished.")

                    elif approval == 'edit':
                        feedback = input("Provide feedback or correction to the agent: ")
                        tool_result = f"User denied the action and provided this feedback: {feedback}"
                    else:
                        tool_result = "User denied permission to execute this tool."
                        print("🛑 Action blocked by user.")

                    messages.append(
                        {"role": "user", "content": f"Tool Execution Result:\n{tool_result}{tool_reinforcement}"})
                    continue

                except json.JSONDecodeError as e:
                    error_msg = (
                        f"Formatting Failure: {str(e)}\n"
                        "Your block string parsing crashed. Remember to output using the minified raw tag:\n"
                        "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"target_file.md\"}}</tool_call>\n"
                        "<payload>\nRAW UNESCAPED CONTENT HERE\n</payload>"
                    )
                    print(f"\n❌ [Parser Interceptor] Halted a syntax loop. Returning loop control to user.")
                    messages.append({"role": "user", "content": error_msg})
                    break

            # --- AUTOMATED FOLLOW-UP TRIGGER ---
            # If the agent reached a plain text response (finished calling tools) and modified a file
            if FORCE_TESTING and file_was_modified and not is_split_mode:
                print("\n[System]: Catching unverified changes. Automatically queuing follow-up test prompt.")
                automated_followup = "Great. Now run the tests or execute the file using `run_cmd` to verify your changes."

            break

        except Exception as e:
            print(f"\n[Error during generation]: {e}")
            break
