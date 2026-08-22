import os
import tempfile
import shutil
import zipfile
import subprocess
import sys
import re
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_coding_task_test(
        input_queue,
        zip_file_path,
        repo_name,
        expected_file=None,
        target_file_path=None,
        check_for_change=False,
        expected_new_files=None,
        run_unittest_file=None,
        run_script_file=None,
        max_calls_limit=50,
        expected_keywords=None,
        custom_output_validator=None
):
    """
    Custom runner for feature-generation tasks. Extracts a repo, runs the agent
    with a specific prompt, validates modifications, unittests, and output files.
    """
    print(f"🧪 Starting Automated Agent Coding Task Test...", flush=True)

    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix="agent_coding_sandbox_")

    source_zip_path = os.path.abspath(os.path.join(original_cwd, zip_file_path))
    if not os.path.exists(source_zip_path):
        shutil.rmtree(test_sandbox)
        pytest.fail(f"Real target zip file not found at: {source_zip_path}")

    with zipfile.ZipFile(source_zip_path, 'r') as zip_ref:
        zip_ref.extractall(test_sandbox)

    # Target the repo directory
    repo_sandbox = os.path.join(test_sandbox, repo_name)

    # Snapshot pristine file if we are checking for modifications
    pristine_contents = {}
    if check_for_change and target_file_path:
        sandbox_dest_path = os.path.join(repo_sandbox, target_file_path)
        if os.path.exists(sandbox_dest_path):
            with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                pristine_contents[sandbox_dest_path] = f.read()

    # State Injection
    simple_coding_agent.session_cwd = repo_sandbox
    simple_coding_agent.FORCE_TESTING = True

    safety_counter = {"calls": 0, "max_calls": max_calls_limit}

    def smart_input_mocker(prompt=""):
        safety_counter["calls"] += 1
        if safety_counter["calls"] > safety_counter["max_calls"]:
            print("\n🛑 [Test Overload] Too many input calls. Forcing exit.", flush=True)
            return "/quit"

        prompt_str = str(prompt).lower()
        if "allow" in prompt_str or "y/n" in prompt_str:
            print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
            return "y"

        if input_queue:
            next_input = input_queue.pop(0)
            print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
            return next_input

        return "/quit"

    try:
        # Move execution directly into the repo folder
        os.chdir(repo_sandbox)

        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                simple_coding_agent.main()
            except SystemExit:
                pass

        # --- Phase 1: Modification & File Generation Verification ---
        print("\n" + "=" * 60, flush=True)
        print("📊 Phase 1: Modification & File Generation Verification", flush=True)

        if check_for_change and target_file_path:
            sandbox_dest_path = os.path.join(repo_sandbox, target_file_path)
            file_changed = False
            if os.path.exists(sandbox_dest_path):
                with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                    if f.read() != pristine_contents.get(sandbox_dest_path, ""):
                        file_changed = True

            if not file_changed:
                pytest.fail(f"❌ FAILED: Target file '{target_file_path}' was not modified.")
            else:
                print(f"✅ SUCCESS: Target file '{target_file_path}' was modified.")

        # Compile list of files to check for existence
        files_to_check = []
        if expected_file:
            files_to_check.append(expected_file)
        if expected_new_files:
            files_to_check.extend(expected_new_files)

        for f_name in files_to_check:
            target_file_path_abs = os.path.join(repo_sandbox, f_name)
            if not os.path.exists(target_file_path_abs):
                parent_fallback = os.path.join(test_sandbox, f_name)
                if os.path.exists(parent_fallback):
                    pytest.fail(f"❌ FAILED: {f_name} was generated in the wrong directory ({parent_fallback}).")
                else:
                    pytest.fail(f"❌ FAILED: {f_name} was not generated at all!")
            print(f"✅ SUCCESS: {f_name} was generated.")

        # --- Phase 2: Content Sanity Check ---
        if expected_file or run_script_file:
            print("\n" + "=" * 60, flush=True)
            print("🕵️  Phase 2: Content Sanity Check", flush=True)
            script_to_check = expected_file or run_script_file
            target_file_path_abs = os.path.join(repo_sandbox, script_to_check)

            if os.path.exists(target_file_path_abs):
                with open(target_file_path_abs, "r", encoding="utf-8") as f:
                    content = f.read()

                if expected_keywords:
                    missing_keywords = [kw for kw in expected_keywords if kw.lower() not in content.lower()]
                    if missing_keywords:
                        print(f"\n--- WRITTEN FILE CONTENT START ---\n{content}\n--- WRITTEN FILE CONTENT END ---\n")
                        pytest.fail(f"❌ FAILED: Script missing expected keywords: {missing_keywords}.")
                    else:
                        print("✅ Content sanity check passed. All expected keywords found.")
                else:
                    print("✅ Content sanity check skipped (no expected keywords provided).")
            else:
                print(f"⚠️ Warning: File {target_file_path_abs} not found for sanity check.")

        # --- Phase 3: Execution Check ---
        print("\n" + "=" * 60, flush=True)
        print("🚀 Phase 3: Execution Check", flush=True)

        # Execute Unittests if specified
        if run_unittest_file:
            try:
                test_file_abs = os.path.join(repo_sandbox, run_unittest_file)
                test_dir = os.path.dirname(test_file_abs)
                test_filename = os.path.basename(test_file_abs)
                test_module = os.path.splitext(test_filename)[0]

                # Update PYTHONPATH so imports within the test directory work
                current_env = os.environ.copy()
                python_paths = [repo_sandbox, test_dir]
                if current_env.get("PYTHONPATH"):
                    python_paths.append(current_env["PYTHONPATH"])
                current_env["PYTHONPATH"] = os.path.pathsep.join(python_paths)

                result = subprocess.run(
                    [sys.executable, "-m", "unittest", test_module],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=test_dir,  # <-- CRITICAL: Run from the test's directory
                    env=current_env
                )
                print(f"Unittest Exit Code: {result.returncode}")
                if result.returncode != 0:
                    pytest.fail(
                        f"❌ FAILED: Unittests failed with code {result.returncode}:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
                else:
                    print(f"✅ SUCCESS: Unittests in {run_unittest_file} passed.")
            except subprocess.TimeoutExpired:
                pytest.fail("❌ FAILED: Unittest execution timed out (>30s).")

        # Execute Script if specified
        script_to_run = run_script_file or expected_file
        if script_to_run:
            try:
                result = subprocess.run(
                    [sys.executable, script_to_run],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=repo_sandbox
                )

                print(f"Exit Code: {result.returncode}")
                if result.stdout:
                    print(f"STDOUT:\n{result.stdout.strip()}")
                if result.stderr:
                    print(f"STDERR:\n{result.stderr.strip()}")

                if result.returncode != 0:
                    pytest.fail(f"❌ FAILED: Script execution crashed with code {result.returncode}:\n{result.stderr}")
                elif not result.stdout.strip():
                    pytest.fail("❌ FAILED: Script executed but produced no output.")

                # Output Sanity Check
                if custom_output_validator:
                    custom_output_validator(result.stdout)
                else:
                    print("✅ Execution check passed! Script ran successfully.")

            except subprocess.TimeoutExpired:
                pytest.fail("❌ FAILED: Script execution timed out (>60s).")
            except Exception as e:
                pytest.fail(f"❌ FAILED: Unexpected error: {e}")

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)


def test_agent_minesweeper_modify_generate_only():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"

    input_queue = [
        "Read the the code in `minesweeper-solve/minesweeper.py`. ",
        "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file.",
        "/send",

        "Good. Now I will need you to change the run_game_loop function so that it has a return value. ",
        "It should return True if the game was won and otherwise it should return False. ",
        "CRITICAL: Just output the python code in a standard ```python markdown block.",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_source,
        repo_name="",
        target_file_path=target_file,
        check_for_change=False,
        max_calls_limit=30
    )


def test_agent_minesweeper_modify_with_patch():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"
    new_test_file = "minesweeper-solve/test_run_game_loop.py"

    input_queue = [
        "Read the code in `minesweeper-solve/minesweeper.py`. "
        "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file.",
        "/send",

        "Good. Now I will need you to change the run_game_loop function so that it has a return value. "
        "It should return True if the game was won and otherwise it should return False. ",
        "Use the `patch_file` tool to make targeted replacements. ",
        "CRITICAL RULES FOR PATCHING:\n"
        "1. Replace the existing `break` statements inside the win/loss/quit conditions with `return True` or `return False`.\n"
        "2. CONTEXT RULE: You MUST include at least 3 lines of surrounding code in your `old_content` to uniquely identify the location. DO NOT just put 'break'.\n"
        "3. You MUST preserve the exact leading spaces (indentation) in both `old_content` and `new_content` to prevent Python IndentationErrors.\n"
        "4. ANTI-LOOP RULE: If `patch_file` fails to find your `old_content`, DO NOT blindly add more lines of context. Instead, use the `read_file` tool again to verify the exact text, spaces, and newlines.",
        "/send",

        # --- PHASE 2: Testing ---
        "Great. Now write a test file named `minesweeper-solve/test_run_game_loop.py` using the `unittest` framework "
        "that tests whether the run_game_loop function in `minesweeper-solve/minesweeper.py` now returns a boolean value. "
        "CRITICAL: Use the `write_file` tool and put the full file content directly inside the JSON `content` field, "
        "properly escaped (use \\n for newlines). Do not use a `<payload>` block.",
        "/send",

        "Run the test suite using your `run_cmd` tool. If any tests fail, use your patching tools to fix the logic. "
        "If they all passed, just reply 'All good'.",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_source,
        repo_name="",
        target_file_path=target_file,
        check_for_change=True,
        expected_new_files=[new_test_file],
        run_unittest_file=new_test_file,
        max_calls_limit=30
    )


# def test_agent_minesweeper_modify_with_rewrite():
#     target_file = "minesweeper-solve/minesweeper.py"
#     zip_source = "test_data/minesweeper-solve.zip"
#
#     input_queue = [
#         "Read the code in `minesweeper-solve/minesweeper.py`. "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file.",
#         "/send",
#
#         "Good. Now I will need you to rewrite the file so that run_game_loop function has a return value. ",
#         "It should return True if the game was won and otherwise it should return False. ",
#         "CRITICAL: Rewrite the entire file, do NOT include just the run_game_loop function.",
#         "CRITICAL: Just output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
#         "/send",
#
#         "Perfect. Now use the `write_file` tool to save the code you just wrote into `minesweeper-solve/minesweeper.py`. "
#         "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
#         "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"minesweeper-solve/minesweeper.py\"}}</tool_call>\n"
#         "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
#         "/send",
#
#         "/quit"
#     ]
#
#     run_automated_agent_test(
#         input_queue=input_queue,
#         target_file_path=target_file,
#         zip_file_path=zip_source,
#         max_calls_limit=30,
#         # validation_test_file=validation_test
#     )
