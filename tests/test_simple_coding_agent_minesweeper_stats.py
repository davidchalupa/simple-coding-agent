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


def check_benchmark_success_rates(stdout: str):
    """
    Specialized validation function for the benchmark script output.
    First ensures that the correct benchmark text was printed, then ensures
    that win rates are parsed and are not identically 0.0
    """
    stdout_lower = stdout.lower()

    # Check 1: Ensure it actually output the benchmark data
    if "dfs" not in stdout_lower or ("rate" not in stdout_lower and "%" not in stdout_lower):
        pytest.fail(
            f"❌ FAILED: Script ran, but output indicates the benchmarks didn't actually execute (missing 'dfs' or 'rate'/'%').\nOutput was: {stdout.strip()}"
        )

    # Check 2: Find all float values or percentages associated with rate/%
    floats = re.findall(r'\b\d+\.\d+\b', stdout)
    percents = re.findall(r'\b(\d+(?:\.\d+)?)\s*%', stdout)

    all_parsed = [float(f) for f in floats] + [float(p) for p in percents]

    if not all_parsed:
        print("⚠️ Warning: Could not explicitly parse floats/percentages for win rates. Relying on base output checks.")
        return

    if all(r == 0.0 for r in all_parsed):
        pytest.fail(
            f"❌ FAILED: Success rates are all 0.0. The agent likely ran games on an empty board.\nSTDOUT:\n{stdout}")

    print(f"✅ Success rate validation passed! Parsed rates: {all_parsed}")


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

    repo_sandbox = os.path.join(test_sandbox, repo_name)

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

        # --- Phase 3: Execution Check ---
        print("\n" + "=" * 60, flush=True)
        print("🚀 Phase 3: Execution Check", flush=True)

        if run_unittest_file:
            try:
                test_file_abs = os.path.join(repo_sandbox, run_unittest_file)
                test_filename = os.path.basename(test_file_abs)
                test_module = os.path.splitext(test_filename)[0]

                result = subprocess.run(
                    [sys.executable, "-m", "unittest", test_module],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=repo_sandbox
                )
                print(f"Unittest Exit Code: {result.returncode}")
                if result.returncode != 0:
                    pytest.fail(
                        f"❌ FAILED: Unittests failed with code {result.returncode}:\n{result.stderr}\n{result.stdout}")
                else:
                    print(f"✅ SUCCESS: Unittests in {run_unittest_file} passed.")
            except subprocess.TimeoutExpired:
                pytest.fail("❌ FAILED: Unittest execution timed out (>30s).")

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

                # Custom Output Sanity Check handled via injected validator
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


def test_agent_minesweeper_stats_write_script_read_main_only_premade():
    """
    Works on minesweeper-solve-with-changes.zip that already has required changes in minesweeper.py.
    Intentionally reads only the main file.
    Keeps the agent free in choice of solution.
    """
    zip_target = "test_data/minesweeper-solve-with-changes.zip"
    repo_name = "minesweeper-solve"

    input_queue = [
        "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
        "Take note of all the `.py` files, especially the ones containing the agent implementations.",
        "/send",

        "Now, use your `read_file` tool to inspect the main python file you just found (the main game file). "
        "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
        "/send",

        "Based on the code you read across those files, write the code for `benchmark.py`. "
        "The script should do the necessary imports, instantiate the game, run 10 games for the Rule-based agent "
        "and the same number for the DFS agent, calculate their success rates, and print the results. \n\n"
        "CRITICAL: Just output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
        "/send",

        "Perfect. Now use the `write_file` tool to save the code you just wrote into `benchmark.py`. "
        "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
        "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"benchmark.py\"}}</tool_call>\n"
        "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
        "/send",

        "Looks good, task complete.",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        expected_file="benchmark.py",
        expected_keywords=["import", "10"],
        custom_output_validator=check_benchmark_success_rates,
        max_calls_limit=60
    )


def test_agent_minesweeper_stats_write_script_read_main_only_premade_minimal():
    """
    Works on minesweeper-solve-with-changes.zip that already has required changes in minesweeper.py.
    Intentionally reads only the main file.
    Keeps the agent free in choice of solution, but strictly forces minimal code and imports.
    """
    zip_target = "test_data/minesweeper-solve-with-changes.zip"
    repo_name = "minesweeper-solve"

    input_queue = [
        "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
        "Take note of all the `.py` files, especially the ones containing the agent implementations.",
        "/send",

        "Now, use your `read_file` tool to inspect the main python file you just found (the main game file). "
        "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
        "/send",

        "Based on the code you read, write the code for `benchmark.py`. "
        "CRITICAL CONSTRAINTS:\n"
        "1. Be extremely minimalistic.\n"
        "2. DO NOT redefine or rewrite any classes or functions from the existing files.\n"
        "3. Imports: You MUST import the game engine functions (like `place_mines`, `compute_counts`, `handle_click`, `run_game_loop`) from `minesweeper.py`. You MUST import the agent callback functions (`ai_get_action` and `dfs_get_action`) from `action_ai_agent.py`.\n"
        "4. DO NOT use the `main_*` launcher functions as agent callbacks. Pass the actual `get_action` callbacks to your game loop.\n"
        "5. DO NOT use `argparse` or require command line arguments. The script MUST automatically run exactly 10 games for the Rule-based agent and the same number for the DFS agent sequentially when executed directly.\n"
        "6. Calculate their success rates and print the results to standard output.\n"
        "\nJust output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
        "/send",

        "Perfect. Now use the `write_file` tool to save the code you just wrote into `benchmark.py`. "
        "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
        "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"benchmark.py\"}}</tool_call>\n"
        "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
        "/send",

        "Looks good, task complete.",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        expected_file="benchmark.py",
        expected_keywords=["import", "10"],
        custom_output_validator=check_benchmark_success_rates,
        max_calls_limit=60
    )


# new integrated test: to be tuned properly
# def test_agent_minesweeper_stats_modify_and_write_script():
#     """
#     1. Reads and modifies minesweeper.py to return a bool from run_game_loop.
#     2. Writes a unittest file proving the boolean return type and executes it.
#     3. Reads agent files and writes benchmark.py.
#     4. Executes the benchmark script and validates stdout reporting (checking against empty board bug).
#     """
#     zip_target = "test_data/minesweeper-solve.zip"
#     repo_name = "minesweeper-solve"
#
#     input_queue = [
#         # --- Part 1: Modification & Unittest ---
#         "Read the code in `minesweeper.py`. "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file.",
#         "/send",
#
#         "Good. Now I will need you to change the run_game_loop function so that it has a return value. "
#         "It should return True if the game was won and otherwise it should return False. ",
#         "Use the `patch_file` tool to make targeted replacements. ",
#         "CRITICAL RULES FOR PATCHING:\n"
#         "1. Replace the existing `break` statements inside the win/loss/quit conditions with `return True` or `return False`.\n"
#         "2. CONTEXT RULE: You MUST include at least 3 lines of surrounding code in your `old_content` to uniquely identify the location. DO NOT just put 'break'.\n"
#         "3. You MUST preserve the exact leading spaces (indentation) in both `old_content` and `new_content` to prevent Python IndentationErrors.\n"
#         "4. ANTI-LOOP RULE: If `patch_file` fails to find your `old_content`, DO NOT blindly add more lines of context. Instead, use the `read_file` tool again to verify the exact text, spaces, and newlines.",
#         "/send",
#
#         "Great. Now write a test file named `test_run_game_loop.py` using the `unittest` framework "
#         "that tests whether the run_game_loop function in `minesweeper.py` now returns a boolean value. "
#         "CRITICAL: Use the `write_file` tool and put the full file content directly inside the JSON `content` field, "
#         "properly escaped (use \\n for newlines). Do not use a `<payload>` block.",
#         "/send",
#
#         "Run the test suite using your `run_cmd` tool. If any tests fail, use your patching tools to fix the logic. "
#         "If they all passed, just reply 'All good'.",
#         "/send",
#
#         # --- Part 2: Context gathering & Benchmark Script Generation ---
#         "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
#         "Take note of all the `.py` files, especially the ones containing the agent implementations.",
#         "/send",
#
#         "Now, use your `read_file` tool to inspect the agent implementation python file you just found. "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
#         "/send",
#
#         "Based on the code you read across those files, write the code for `benchmark.py`. "
#         "CONSTRAINTS:\n"
#         "1. Imports: import `run_game_loop`, `place_mines`, and `compute_counts` from `minesweeper.py`. Import the `get_action` callbacks (like ai_get_action and dfs_get_action) from `action_ai_agent.py`.\n"
#         "2. Game Setup: You MUST properly initialize the board for each game by calling `place_mines` (e.g. 10 mines) and `compute_counts`. Do not run games on an empty board.\n"
#         "3. Do not use command line arguments. The script MUST automatically run exactly 10 games for the Rule-based agent and 10 games for the DFS agent when executed directly.\n"
#         "4. Calculate their success rates by keeping track of the new boolean return values from `run_game_loop`.\n"
#         "5. Print the results to stdout showing the 'rate' or '%' and mentioning 'dfs'.\n\n"
#         "CRITICAL: Just output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
#         "/send",
#
#         "Perfect. Now use the `write_file` tool to save the code you just wrote into `benchmark.py`. "
#         "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
#         "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"benchmark.py\"}}</tool_call>\n"
#         "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
#         "/send",
#
#         "Looks good, task complete.",
#         "/send",
#
#         "/quit"
#     ]
#
#     run_automated_coding_task_test(
#         input_queue=input_queue,
#         zip_file_path=zip_target,
#         repo_name=repo_name,
#         target_file_path="minesweeper.py",
#         check_for_change=True,
#         expected_new_files=["test_run_game_loop.py", "benchmark.py"],
#         run_unittest_file="test_run_game_loop.py",
#         run_script_file="benchmark.py",
#         max_calls_limit=80,
#         custom_output_validator=check_benchmark_success_rates
#     )

