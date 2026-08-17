import sys
import os
import tempfile
import shutil
import zipfile
import subprocess
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_agent_test(
        input_queue,
        target_file_path,
        zip_file_path,
        max_calls_limit=30,
        expected_strings=None,
        validation_test_file=None,
        check_for_change=True,
        expected_new_files=None,
):
    """
    General-purpose automated runner for testing agent file modifications in an isolated sandbox.
    Verifies execution safety, file syntax, expected content changes, and optional unit tests.
    Uses a .zip file as the source environment.
    """
    print(f"🧪 Starting Automated Agent Flow Test for: {target_file_path}...", flush=True)

    original_cwd = os.getcwd()
    source_zip_path = os.path.abspath(os.path.join(original_cwd, zip_file_path))

    if not os.path.exists(source_zip_path):
        pytest.fail(f"Source zip file not found at: {source_zip_path}")

    # Use TemporaryDirectory to guarantee cleanup
    with tempfile.TemporaryDirectory(prefix="agent_test_sandbox_") as test_sandbox:
        print(f"📁 Created temporary sandbox at: {test_sandbox}", flush=True)

        # --- Extract Zip into Sandbox ---
        with zipfile.ZipFile(source_zip_path, 'r') as zip_ref:
            zip_ref.extractall(test_sandbox)
        print(f"🌱 Extracted {zip_file_path} into sandbox.", flush=True)

        sandbox_dest_path = os.path.join(test_sandbox, target_file_path)
        if not os.path.exists(sandbox_dest_path):
            pytest.fail(f"Target path not found in extracted sandbox: {sandbox_dest_path}")

        # --- Snapshot Pristine Contents (For Phase 1 Change Verification) ---
        pristine_contents = {}
        if os.path.isfile(sandbox_dest_path):
            with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                pristine_contents[sandbox_dest_path] = f.read()
        elif os.path.isdir(sandbox_dest_path):
            for root, _, files in os.walk(sandbox_dest_path):
                for file in files:
                    s_dest = os.path.join(root, file)
                    with open(s_dest, "r", encoding="utf-8") as f:
                        pristine_contents[s_dest] = f.read()

        # --- State Setup & Injection ---
        orig_session_cwd = getattr(simple_coding_agent, "session_cwd", None)
        orig_force_testing = getattr(simple_coding_agent, "FORCE_TESTING", False)

        simple_coding_agent.messages = []
        simple_coding_agent.is_split_mode = False
        simple_coding_agent.is_execute_mode = False
        simple_coding_agent.sandbox_directory = None
        simple_coding_agent.automated_followup = None
        simple_coding_agent.has_prompted_for_tests = False

        simple_coding_agent.session_cwd = test_sandbox
        simple_coding_agent.FORCE_TESTING = False

        safety_counter = {"calls": 0, "max_calls": max_calls_limit}

        def smart_input_mocker(prompt=""):
            safety_counter["calls"] += 1
            if safety_counter["calls"] > safety_counter["max_calls"]:
                print("\n🛑 [Test Overload] Exceeded maximum input calls limit. Forcing exit.", flush=True)
                return "/quit"

            prompt_str = str(prompt).lower()

            if "allow" in prompt_str or "y/n" in prompt_str or "edit" in prompt_str:
                print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
                return "y"

            if input_queue:
                next_input = input_queue.pop(0)
                print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
                return next_input

            return "/quit"

        try:
            os.chdir(test_sandbox)

            with patch("builtins.input", side_effect=smart_input_mocker):
                try:
                    simple_coding_agent.main()
                except SystemExit as e:
                    print(f"\n🏁 Agent session terminated with code: {e.code}", flush=True)

            # --- Phase 1: File Modification Audit ---
            if check_for_change:
                print("\n" + "=" * 60, flush=True)
                print("📊 Phase 1: Modification & Content Verification", flush=True)

                file_changed = False
                if os.path.isfile(sandbox_dest_path):
                    with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                        modified_content = f.read()
                    if modified_content != pristine_contents.get(sandbox_dest_path, ""):
                        file_changed = True

                elif os.path.isdir(sandbox_dest_path):
                    for root, _, files in os.walk(sandbox_dest_path):
                        for file in files:
                            s_dest = os.path.join(root, file)
                            if s_dest not in pristine_contents:
                                file_changed = True  # New file created
                                break
                            with open(s_dest, "r", encoding="utf-8") as fd:
                                if fd.read() != pristine_contents[s_dest]:
                                    file_changed = True
                                    break
                        if file_changed:
                            break

                if not file_changed:
                    pytest.fail("❌ Test failed: The agent did not make any actual changes to the target code.")
                else:
                    print(f"✅ Target file/directory content was successfully modified by the agent.", flush=True)

                if expected_strings and os.path.isfile(sandbox_dest_path):
                    with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                        modified_content = f.read()

                    for expected in expected_strings:
                        if expected not in modified_content:
                            print(f"❌ Missing expected content fragment: '{expected}'", flush=True)
                            pytest.fail(
                                f"Agent failed content verification. '{expected}' was not found in modified file.")
                        else:
                            print(f"✅ Content fragment found: '{expected}'", flush=True)

            # --- Phase 1b: Expected New File(s) Existence Check ---
            if expected_new_files:
                print("\n" + "=" * 60, flush=True)
                print("📄 Phase 1b: New File Existence Verification", flush=True)

                for rel_path in expected_new_files:
                    abs_path = os.path.join(test_sandbox, rel_path)
                    if not os.path.exists(abs_path):
                        print(f"❌ Expected file not found: '{rel_path}'", flush=True)
                        pytest.fail(f"Agent did not create expected file: '{rel_path}'")
                    else:
                        print(f"✅ Expected file exists: '{rel_path}'", flush=True)

            # --- Phase 2: Syntax Verification ---
            print("\n" + "=" * 60, flush=True)
            print("🕵️  Phase 2: Linter & Syntax Verification", flush=True)

            current_env = os.environ.copy()
            current_env["PYTHONPATH"] = os.path.pathsep.join([test_sandbox, current_env.get("PYTHONPATH", "")])

            check_passed = True
            for root, _, files in os.walk(test_sandbox):
                for file in files:
                    if file.endswith(".py"):
                        file_path = os.path.join(root, file)
                        result = subprocess.run(
                            [sys.executable, "-m", "py_compile", file_path],
                            capture_output=True,
                            text=True,
                            env=current_env
                        )
                        if result.returncode != 0:
                            print(f"❌ SYNTAX ERROR in {file}:\n{result.stderr}", flush=True)
                            check_passed = False
                        else:
                            print(f"✅ Syntax valid: {file}", flush=True)

            if not check_passed:
                pytest.fail("Syntax verification failed on modified code!")

            # --- Phase 3: Optional Logic Verification (Unit Tests) ---
            if validation_test_file:
                print("\n" + "=" * 60, flush=True)
                print("🧪 Phase 3: Unit Test Logic Verification", flush=True)

                test_source_path = os.path.join(original_cwd, validation_test_file)
                if not os.path.exists(test_source_path):
                    pytest.fail(f"Validation test file not found at: {test_source_path}")

                test_sandbox_dest = os.path.join(test_sandbox, validation_test_file)
                os.makedirs(os.path.dirname(test_sandbox_dest), exist_ok=True)
                shutil.copy2(test_source_path, test_sandbox_dest)

                target_dir = os.path.dirname(sandbox_dest_path) if os.path.isfile(
                    sandbox_dest_path) else sandbox_dest_path
                os.chdir(target_dir)
                current_env["PYTHONPATH"] = os.path.pathsep.join([target_dir, current_env.get("PYTHONPATH", "")])

                result = subprocess.run(
                    [sys.executable, "-m", "unittest", os.path.basename(validation_test_file)],
                    capture_output=True,
                    text=True,
                    env=current_env
                )

                if result.returncode != 0:
                    print(f"❌ LOGIC VERIFICATION FAILED:\n{result.stdout}\n{result.stderr}", flush=True)
                    pytest.fail("Unit tests failed on the modified code!")
                else:
                    print(f"✅ Logic Verification Passed.\n{result.stderr}", flush=True)

        finally:
            os.chdir(original_cwd)
            simple_coding_agent.session_cwd = orig_session_cwd
            simple_coding_agent.FORCE_TESTING = orig_force_testing
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

    run_automated_agent_test(
        input_queue=input_queue,
        target_file_path=target_file,
        zip_file_path=zip_source,
        max_calls_limit=30,
        check_for_change=False,
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

        "/quit"
    ]

    run_automated_agent_test(
        input_queue=input_queue,
        target_file_path=target_file,
        zip_file_path=zip_source,
        max_calls_limit=30,
        expected_new_files=[new_test_file],
        # validation_test_file=validation_test
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
