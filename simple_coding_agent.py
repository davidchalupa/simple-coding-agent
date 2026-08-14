import sys
import os
import json
import re
import shutil
import psutil
import argparse

import llama_cpp
from llama_cpp import Llama
from pathlib import Path

from coding_agent.welcome_banner import display_welcome_banner
from coding_agent.input_handler import get_user_prompt
from coding_agent.tool_definitions import read_file, extract_code_blocks
from coding_agent.execute_tool import execute_tool
from coding_agent.system_prompt_builder import build_system_prompt
from coding_agent.native_helpers import get_repo_structure, generate_requirements_native, gather_deep_context, \
    gather_deep_context_ast
from coding_agent import hidden_readme_prompt_builder
from coding_agent import file_splitter
from coding_agent import native_linter
from coding_agent import payload_parser

# 1. Configuration & Model Registry
script_dir = Path(__file__).resolve().parent

MODEL_REGISTRY = {
    "qwen2.5": {
        "filename": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        "display_name": "Qwen 2.5 Coder 7B",
        "chat_format": "chatml",
        "max_context": 32768,
        "gpu_layers": -1  # -1 attempts to offload entirely to GPU
    },
    "hermes3": {
        "filename": "Hermes-3-Llama-3.1-8B.Q4_K_M.gguf",
        "display_name": "Hermes 3 (Llama 3.1 8B)",
        "stop": ["<|im_end|>", "<|eot_id|>", "<|endoftext|>"],
        "temperature": 0.2,
        "max_context": 32768,
        "gpu_layers": -1,  # -1 or 99 offloads all layers to your RTX 5050 GPU
        "chat_format": "chatml"  # Hermes 3 uses standard ChatML formatting
    }
}

# 2. CLI Argument Parsing
parser = argparse.ArgumentParser(description="Coding Agent CLI")
parser.add_argument("--model", type=str, default="qwen2.5", choices=MODEL_REGISTRY.keys(),
                    help="Select the model to run from the registry.")
parser.add_argument("--disable-replace", action="store_true",
                    help="Disable the replace_lines tool (forces full file rewrites).")
parser.add_argument("--force-testing", action="store_true", help="Force automated test prompting.")
args, unknown = parser.parse_known_args()

# Extract selected configuration
active_config = MODEL_REGISTRY[args.model]
target_path = script_dir / "models" / active_config["filename"]
loaded_model_name = active_config["display_name"]
CONTEXT_WINDOW = active_config["max_context"]

# Enabled by default. Overridden only if the user explicitly passes --disable-replace
ALLOW_PATCH = not args.disable_replace
FORCE_TESTING = args.force_testing

# Global State Placeholders
llm = None
SYSTEM_PROMPT = ""
messages = []
session_cwd = os.getcwd()

# --- SANDBOX STATE TRACKING ---
is_split_mode = False
is_execute_mode = False  # Track if we are using AST interception
original_split_file = None
sandbox_directory = None
automated_followup = None  # Buffer for system-generated prompt injections
has_prompted_for_tests = False


def get_system_ram_gb():
    """Returns total system RAM in gigabytes."""
    return psutil.virtual_memory().total / (1024 ** 3)


def initialize_agent():
    """Initializes LLM dynamically according to registry config, GPU, and RAM."""
    global llm, SYSTEM_PROMPT, messages, CONTEXT_WINDOW

    if llm is not None:
        return

    if not os.path.exists(target_path):
        print(f"❌ Error: Model file not found at {target_path}")
        sys.exit(1)

    print(f"Loading {loaded_model_name}...")

    total_ram = get_system_ram_gb()

    # Cap the context sizes so we don't request more than the model supports
    max_ctx = active_config["max_context"]
    base_gpu_contexts = [32768, 16384, 8192]
    gpu_contexts = sorted(list(set([min(ctx, max_ctx) for ctx in base_gpu_contexts])), reverse=True)

    # Simple CPU fallback contexts based on RAM
    if total_ram >= 24:
        cpu_contexts = gpu_contexts
    elif total_ram >= 12:
        cpu_contexts = [ctx for ctx in gpu_contexts if ctx <= 16384]
    else:
        cpu_contexts = [ctx for ctx in gpu_contexts if ctx <= 8192]

    has_gpu = getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)()

    # --- 1. ATTEMPT GPU LOAD ---
    if has_gpu:
        for ctx_size in gpu_contexts:
            try:
                print(f"🔄 Attempting GPU load with {ctx_size} context...")
                llm = Llama(
                    model_path=str(target_path),
                    n_ctx=ctx_size,
                    n_threads=6,
                    n_batch=512,
                    n_gpu_layers=active_config["gpu_layers"],
                    chat_format=active_config["chat_format"],  # <-- CRITICAL FOR MULTI-MODEL
                    flash_attn=True,
                    verbose=False
                )
                CONTEXT_WINDOW = ctx_size
                print(f"🚀 Loaded on GPU (Context: {CONTEXT_WINDOW}).")
                break
            except Exception as e:
                print(f"⚠️ GPU load failed at {ctx_size} context: {e}")

    # --- 2. CPU FALLBACK ---
    if llm is None:
        print(f"🐢 Running on CPU (Detected System RAM: {total_ram:.1f} GB)...")
        for ctx_size in cpu_contexts:
            try:
                print(f"🔄 Attempting CPU load with {ctx_size} context...")
                llm = Llama(
                    model_path=str(target_path),
                    n_ctx=ctx_size,
                    n_threads=6,
                    n_batch=512,
                    n_gpu_layers=0,
                    chat_format=active_config["chat_format"],  # <-- CRITICAL FOR MULTI-MODEL
                    verbose=False
                )
                CONTEXT_WINDOW = ctx_size
                print(f"🐢 Loaded on CPU (Context: {CONTEXT_WINDOW}).")
                break
            except Exception as e_cpu:
                print(f"⚠️ CPU allocation failed at {ctx_size} context: {e_cpu}")

    if llm is None:
        print("❌ Critical Error: Unable to initialize model on GPU or CPU.")
        sys.exit(1)

    # State Setup
    SYSTEM_PROMPT = build_system_prompt(ALLOW_PATCH)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]


def find_last_code_block(messages):
    """Scan backwards through assistant turns for the most recent fenced code block."""
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            match = re.search(r"```(?:python)?\s*\n(.*?)\n```", msg["content"], re.DOTALL)
            if match:
                return match.group(1)
    return None

def main():
    global messages, session_cwd, is_split_mode, is_execute_mode, original_split_file, sandbox_directory, automated_followup, has_prompted_for_tests

    initialize_agent()

    display_welcome_banner(loaded_model_name, ALLOW_PATCH)

    # 6. Main Agent Loop
    while True:
        user_input = ""

        # Check if we have an automated follow-up prompt queued
        if automated_followup:
            user_input = automated_followup
            automated_followup = None
            print(f"\n[Automated User]: {user_input}")
        else:
            # smart input handler
            user_input = get_user_prompt()

            if user_input == "/quit":
                print("Exiting. Goodbye!")
                sys.exit(0)

            if user_input == "/clear":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                session_cwd = os.getcwd()
                is_split_mode = False
                is_execute_mode = False
                original_split_file = None
                sandbox_directory = None
                automated_followup = None
                has_prompted_for_tests = False
                print("🧹 Memory and environment completely cleared!")
                continue

            if user_input == "/cancel":
                print("❌ Current draft discarded.")
                continue

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

            # Deep Mode Trigger Interceptor
            code_summary = None
            cli_help = None
            if deep_ast_focus:
                print(
                    "👀 [Mode Change] Experimental Dispatcher: Extracting AST interfaces and auto-routing based on size...")
                code_summary, cli_help = gather_deep_context_ast(abs_target_dir)
            elif deep_focus:
                print("👀 [Mode Change] Deep Scan: Extracting script code segments and querying CLI help hooks...")
                code_summary, cli_help = gather_deep_context(abs_target_dir)

            strategy_steps = hidden_readme_prompt_builder.build_strategy_steps(
                readme_path, ALLOW_PATCH,
                deep_focus=(deep_focus or deep_ast_focus)
            )

            hidden_readme_prompt = hidden_readme_prompt_builder.build_hidden_readme_prompt(
                abs_target_dir, repo_tree, existing_readme, strategy_steps, code_summary=code_summary,
                cli_help=cli_help
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
                    f"\n⚠️  [WARNING] Execution Mode Active: System will use AST natively based on LLM JSON mapping.")
                print("   This isolates the agent from hallucinating logic blocks.")
                print(f"🔍 Initializing Sandbox and Parsing AST structure for {abs_target_file}...")
            else:
                print(f"\n🔍 Initializing Sandbox (Advisor Mode) for {abs_target_file}...")

            # 1. Setup sandbox tracking
            _, sandbox_directory = file_splitter.setup_refactor_sandbox(abs_target_file)
            original_split_file = abs_target_file
            is_split_mode = True
            is_execute_mode = execute_mode

            # 2. Divert agent's current working directory to the sandbox!
            session_cwd = sandbox_directory

            # Pass the flag to the prompt builder
            split_prompt = file_splitter.build_split_prompt(abs_target_file, session_cwd, execute_mode=execute_mode)

            if not execute_mode:
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
            # --- CONTEXT TOKEN GUARDRAIL ---
            try:
                # Calculate exact token usage of history + ChatML format buffer
                current_tokens = sum(len(llm.tokenize(m["content"].encode('utf-8'))) + 10 for m in messages)

                if current_tokens > CONTEXT_WINDOW:
                    print(f"\n🚨 [MEMORY OVERLOAD]: Prompt size is {current_tokens} tokens (Limit: {CONTEXT_WINDOW}).")
                    print("   The agent will likely hallucinate or output truncated JSON.")
                    print("   Consider using '/clear' or falling back to '--deep-ast' instead of '--deep'.")
                elif current_tokens > int(CONTEXT_WINDOW * 0.85):
                    usage_percent = (current_tokens / CONTEXT_WINDOW) * 100
                    print(
                        f"\n⚠️  [MEMORY WARNING]: Approaching context limit ({current_tokens}/{CONTEXT_WINDOW} tokens, {usage_percent:.1f}%).")
            except Exception:
                # Failsafe if the tokenizer crashes so the main loop survives
                pass
            # -------------------------------

            print(f"\n[Agent]: ", end="", flush=True)
            response_content = ""

            try:
                stream = llm.create_chat_completion(
                    messages=messages, stream=True, temperature=0.1,
                )

                finish_reason = None

                # --- INNER STREAM WRAPPED FOR INTERRUPT HANDLING ---
                try:
                    for chunk in stream:
                        choice = chunk['choices'][0]
                        if choice.get('finish_reason'):
                            finish_reason = choice['finish_reason']

                        delta = choice.get('delta')
                        if 'content' in delta:
                            piece = delta['content']
                            print(piece, end="", flush=True)
                            response_content += piece

                except KeyboardInterrupt:
                    print("\n\n🛑 [Generation Interrupted by User]")

                    # CRITICAL CLEANUP: If the agent was mid-tool-call, strip the broken JSON
                    if "<tool_call> " in response_content and "</tool_call>" not in response_content:
                        response_content = re.sub(r"<tool_call>.*$", "", response_content, flags=re.DOTALL).strip()

                    if response_content:
                        messages.append({"role": "assistant", "content": response_content + " [Interrupted]"})
                    break

                is_truncated = (finish_reason == "length")

                if "<tool_call>" in response_content and "</tool_call>" not in response_content:
                    response_content += "</tool_call>"
                    print("</tool_call>", end="", flush=True)

                print()
                messages.append({"role": "assistant", "content": response_content})

                # --- NEW: AST EXTRACTION INTERCEPTOR FOR EXECUTE MODE ---
                if is_split_mode and is_execute_mode and "```json" in response_content:
                    json_match = re.search(r"```json\s*\n(.*?)\n```", response_content, re.DOTALL)
                    if json_match:
                        try:
                            split_plan = json.loads(json_match.group(1))
                            print("\n⚙️  [System] Intercepted JSON routing plan. Executing AST extraction natively...")

                            extraction_results = []
                            for target_filename, blocks in split_plan.items():
                                if not isinstance(blocks, list):
                                    continue
                                target_filepath = os.path.join(sandbox_directory, target_filename)
                                # Deterministically extract the blocks without LLM generation
                                result = extract_code_blocks(original_split_file, target_filepath, blocks)
                                extraction_results.append(f"[{target_filename}]: {result}")

                            report = "\n".join(extraction_results)
                            print(report)

                            system_feedback = (
                                f"System Alert: AST Extraction successfully executed based on your JSON map.\n"
                                f"Results:\n{report}\n\n"
                                f"Next Step: The files contain the logic, but lack dependencies. Use `replace_lines` or `write_file` "
                                f"to add the necessary `import` statements at the top of these newly created files. "
                                f"Additionally, update the original file to import these extracted components and remove the old extracted code. "
                                f"When the refactor is fully wired and syntactically correct, output 'Refactor Phase Complete'."
                            )

                            messages.append({"role": "user", "content": system_feedback})
                            is_execute_mode = False  # Reset so we don't extract twice on the next loop iteration
                            continue  # Jump to next loop iteration so the agent can write imports

                        except json.JSONDecodeError:
                            print("\n❌ [System] Failed to parse JSON plan.")
                            messages.append({
                                "role": "user",
                                "content": "System Alert: Your JSON block was invalid and could not be parsed. Please output ONLY valid JSON in the ```json block."
                            })
                            continue

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
                                            "Use your replace_lines or write_file tool to add the missing imports at the top of the file."
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
                        is_execute_mode = False
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

                # 1. Extract and parse tool request using the new wrapper
                tool_request = payload_parser.extract_tool_call(response_content, allow_patch=ALLOW_PATCH)

                if tool_request:
                    try:
                        tool_name = tool_request.get("name")
                        tool_args = tool_request.get("args", {})

                        # 2. Keep the truncation check
                        if is_truncated and tool_name == "write_file":
                            raise json.JSONDecodeError("Incomplete payload due to context limit truncation.", "", 0)

                        # 3. Keep path resolution
                        # 3. Keep path resolution
                        # Resolve relative file and directory paths against active session_cwd
                        if "filepath" in tool_args and not os.path.isabs(tool_args["filepath"]):
                            tool_args["filepath"] = os.path.abspath(os.path.join(session_cwd, tool_args["filepath"]))

                        if "dir_path" in tool_args and not os.path.isabs(tool_args["dir_path"]):
                            tool_args["dir_path"] = os.path.abspath(os.path.join(session_cwd, tool_args["dir_path"]))

                        # 4. Keep empty file check
                        if tool_name in ["write_file", "append_file", "replace_lines"]:
                            content = tool_args.get('content', '')
                            clean_content = re.sub(r'```[a-zA-Z]*\s*```', '', content).strip()

                            if not clean_content:
                                recovered = find_last_code_block(messages)
                                if recovered and recovered.strip():
                                    print(
                                        f"🔧 [Recovery] Empty payload detected — reusing last drafted code block for {tool_name}.")
                                    tool_args["content"] = recovered
                                    # fall through to execute the write with recovered content instead of blocking
                                else:
                                    print(f"🛑 [Parser Interceptor] Blocked an empty {tool_name} operation.")
                                    messages.append({
                                        "role": "user",
                                        "content": (
                                            f"System Alert: Your {tool_name} call FAILED — no file was written because the payload was empty. "
                                            f"This is NOT the same as having nothing to do. You must retry immediately using the exact format:\n"
                                            f"<tool_call>{{\"name\": \"{tool_name}\", \"args\": {{\"filepath\": \"...\"}}}}</tool_call>\n"
                                            f"<payload>\nYOUR ACTUAL FILE CONTENT HERE\n</payload>\n"
                                            f"Do not claim the file was saved — it was not. Do not respond in plain text until the write actually succeeds."
                                        )
                                    })
                                    continue

                        print(f"\n⚠️  AGENT REQUESTS PERMISSION TO EXECUTE: {tool_name}")
                        if tool_name in ["write_file", "append_file"]:
                            print(f"Resolved Target File: {tool_args.get('filepath')}")
                            print("Content Snippet: \n" + "-" * 20)
                            print(tool_args.get('content', '')[:300] + "\n...[truncated snippet]\n" + "-" * 20)
                        elif tool_name == "replace_lines":
                            print(
                                f"Replacing lines {tool_args.get('start_line')} to {tool_args.get('end_line')} in: {tool_args.get('filepath')}")
                            print("New Content Snippet: \n" + "-" * 20)
                            print(tool_args.get('content', '')[:300] + "\n...[truncated snippet]\n" + "-" * 20)
                        else:
                            print(f"Arguments: {tool_args}")

                        approval = input("Allow this action? (y/n/edit): ").strip().lower()

                        tool_result = ""
                        tool_reinforcement = ""

                        if approval == 'y':
                            # --- REFACTORED TOOL DISPATCH ---
                            tool_result, tool_reinforcement, was_mod = execute_tool(tool_name, tool_args, is_split_mode)
                            if was_mod:
                                file_was_modified = True
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
                if FORCE_TESTING and file_was_modified and not is_split_mode:
                    raw_path = tool_args.get("filepath", "")

                    if raw_path:
                        path_obj = Path(raw_path)
                        filename = path_obj.name.lower()
                        is_test_filename = filename.startswith("test_") or filename.endswith("_test.py")
                        is_test_file = is_test_filename and filename.endswith(".py")
                    else:
                        is_test_file = False

                    if is_test_file and not has_prompted_for_tests:
                        print(
                            "\n[System]: Catching unverified test changes. Automatically queuing follow-up test prompt.")
                        # Use the absolute path to the current interpreter (e.g., inside the .venv)
                        # Replace backslashes with forward slashes to prevent JSON escaping crashes on Windows
                        safe_py_exec = sys.executable.replace("\\", "/")

                        automated_followup = (
                            f"Great. Now use `run_cmd` to run this test file (e.g., using `\"{safe_py_exec}\" -m unittest`) to verify your logic. "
                            "CRITICAL: If a test fails, you must follow these steps strictly:\n"
                            "1. Do NOT output a tool call immediately.\n"
                            "2. Audit the failing test case: Does the test input actually violate the original problem constraints?\n"
                            "3. If the test itself is invalid, use `replace_lines` or `write_file` to DELETE or FIX the bad test in the test file.\n"
                            "4. If the test is valid, analyze why your source code failed, and fix the source code.\n"
                            "5. Rerun the tests until they pass."
                        )
                        has_prompted_for_tests = True
                    else:
                        print("\n[System]: Main script written / modified.")
                        automated_followup = None

                break

            except Exception as e:
                print(f"\n[Error during generation]: {e}")
                break


if __name__ == "__main__":
    main()
