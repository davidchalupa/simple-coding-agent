import sys
import os
import tempfile
import shutil
import subprocess
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_agent_test(
        input_queue,
        target_file_path,
        max_calls_limit=30,
        expected_strings=None,
        validation_test_file=None,
        check_for_change=True,
):
    """
    General-purpose automated runner for testing agent file modifications in an isolated sandbox.
    Verifies execution safety, file syntax, expected content changes, and optional unit tests.
    """
    print(f"🧪 Starting Automated Agent Flow Test for: {target_file_path}...", flush=True)

    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix="agent_test_sandbox_")
    print(f"📁 Created temporary sandbox at: {test_sandbox}", flush=True)

    # --- Copy target file/directory into sandbox ---
    source_path = os.path.join(original_cwd, target_file_path)
    if not os.path.exists(source_path):
        shutil.rmtree(test_sandbox)
        pytest.fail(f"Target file not found at: {source_path}")

    sandbox_dest_path = os.path.join(test_sandbox, target_file_path)
    os.makedirs(os.path.dirname(sandbox_dest_path), exist_ok=True)

    if os.path.isdir(source_path):
        shutil.copytree(source_path, sandbox_dest_path, dirs_exist_ok=True)
    else:
        shutil.copy2(source_path, sandbox_dest_path)

    print(f"🌱 Copied target to sandbox: {target_file_path}", flush=True)

    # --- State Setup ---
    simple_coding_agent.session_cwd = test_sandbox
    simple_coding_agent.FORCE_TESTING = False

    # # disallowing patch tool for now, as the agent tends to just guess line numbers
    # # forcing the agent to rewrite the entire file this way
    # simple_coding_agent.ALLOW_PATCH = False

    safety_counter = {"calls": 0, "max_calls": max_calls_limit}

    def smart_input_mocker(prompt=""):
        safety_counter["calls"] += 1
        if safety_counter["calls"] > safety_counter["max_calls"]:
            print("\n🛑 [Test Overload] Exceeded maximum input calls limit. Forcing exit.", flush=True)
            return "/quit"

        prompt_str = str(prompt).lower()

        # Auto-approve tool execution and file replacements
        if "allow" in prompt_str or "y/n" in prompt_str or "edit" in prompt_str:
            print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
            return "y"

        if input_queue:
            next_input = input_queue.pop(0)
            print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
            return next_input

        return "/quit"

    try:
        # Move execution context into sandbox
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

            # Physically check if the file contents have actually changed
            file_changed = False
            if os.path.isfile(sandbox_dest_path):
                with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                    modified_content = f.read()
                with open(source_path, "r", encoding="utf-8") as f:
                    original_content = f.read()

                if modified_content != original_content:
                    file_changed = True

            elif os.path.isdir(sandbox_dest_path):
                # If it's a directory, check if any file inside was modified or created
                for root, _, files in os.walk(sandbox_dest_path):
                    for file in files:
                        s_dest = os.path.join(root, file)
                        s_src = os.path.join(source_path, os.path.relpath(s_dest, sandbox_dest_path))
                        if os.path.exists(s_src):
                            with open(s_dest, "r", encoding="utf-8") as fd, open(s_src, "r", encoding="utf-8") as fs:
                                if fd.read() != fs.read():
                                    file_changed = True
                                    break
                        else:
                            file_changed = True  # New file was created
                    if file_changed:
                        break

            if not file_changed:
                pytest.fail("❌ Test failed: The agent did not make any actual changes to the target code.")
            else:
                print(f"✅ Target file/directory content was successfully modified by the agent.", flush=True)

            # Content String Assertions
            if expected_strings and os.path.isfile(sandbox_dest_path):
                with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                    modified_content = f.read()

                for expected in expected_strings:
                    if expected not in modified_content:
                        print(f"❌ Missing expected content fragment: '{expected}'", flush=True)
                        pytest.fail(f"Agent failed content verification. '{expected}' was not found in modified file.")
                    else:
                        print(f"✅ Content fragment found: '{expected}'", flush=True)

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

            target_dir = os.path.dirname(sandbox_dest_path) if os.path.isfile(sandbox_dest_path) else sandbox_dest_path
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
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)


def test_agent_minesweeper_modify_generate_only():
    target_file = "test_data/test_minesweeper/minesweeper.py"

    input_queue = [
        "Read the the code in `test_data/test_minesweeper/minesweeper.py`. ",
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
        max_calls_limit=30,
        check_for_change=False,
    )


# def test_agent_minesweeper_modify_with_rewrite():
#     target_file = "test_data/test_minesweeper/minesweeper.py"
#
#     input_queue = [
#         "Read the code in `test_data/test_minesweeper/minesweeper.py`. "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file.",
#         "/send",
#
#         "Good. Now I will need you to rewrite the file so that run_game_loop function has a return value. ",
#         "It should return True if the game was won and otherwise it should return False. ",
#         "CRITICAL: Rewrite the entire file, do NOT include just the run_game_loop function.",
#         "CRITICAL: Just output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
#         "/send",
#
#         "Perfect. Now use the `write_file` tool to save the code you just wrote into `test_data/test_minesweeper/minesweeper.py`. "
#         "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
#         "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"test_data/test_minesweeper/minesweeper.py\"}}</tool_call>\n"
#         "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
#         "/send",
#
#         # Turn 3: Graceful exit
#         "/quit"
#     ]
#
#     run_automated_agent_test(
#         input_queue=input_queue,
#         target_file_path=target_file,
#         max_calls_limit=30,
#         # validation_test_file=validation_test  # Uncomment if you have a Pytest/Unittest file for this
#     )


# def test_agent_minesweeper_modify_with_patch():
#     target_file = "test_data/test_minesweeper/minesweeper.py"
#
#     # patch-based formulation - not ready yet
#     input_queue = [
#         "Read the the code in `test_data/test_minesweeper/minesweeper.py`. ",
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file."
#         "/send",
#
#         "Good. Now I will need you to change the run_game_loop function so that it has a return value. ",
#         "It should return True if the game was won and otherwise it should return False. ",
#         "Identify the places where return statements should be placed and make targeted replacements using the `patch_file` tool.",
#         "/send",
#
#         "/quit"
#     ]
#
#     run_automated_agent_test(
#         input_queue=input_queue,
#         target_file_path=target_file,
#         max_calls_limit=30,
#         # validation_test_file=validation_test  # Uncomment if you have a Pytest/Unittest file for this
#     )
