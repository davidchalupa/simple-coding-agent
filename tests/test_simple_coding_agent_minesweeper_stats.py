import os
import tempfile
import shutil
import zipfile
import subprocess
import sys
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_coding_task_test(input_queue, zip_file_path, repo_name, expected_file, max_calls_limit=50):
    """
    Custom runner for feature-generation tasks. Extracts a repo, runs the agent
    with a specific prompt, and validates the output file.
    """
    print(f"🧪 Starting Automated Agent Coding Task Test for {expected_file}...", flush=True)

    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix="agent_coding_sandbox_")

    source_zip_path = os.path.abspath(os.path.join(original_cwd, zip_file_path))
    if not os.path.exists(source_zip_path):
        shutil.rmtree(test_sandbox)
        pytest.fail(f"Real target zip file not found at: {source_zip_path}")

    with zipfile.ZipFile(source_zip_path, 'r') as zip_ref:
        zip_ref.extractall(test_sandbox)

    # --- PATH FIX: Target the repo directly ---
    repo_sandbox = os.path.join(test_sandbox, repo_name)

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

        # --- Phase 1: File Generation Verification ---
        print("\n" + "=" * 60, flush=True)
        print("📊 Phase 1: File Generation Verification", flush=True)
        target_file_path = os.path.join(repo_sandbox, expected_file)

        if not os.path.exists(target_file_path):
            parent_fallback = os.path.join(test_sandbox, expected_file)
            if os.path.exists(parent_fallback):
                pytest.fail(f"❌ FAILED: {expected_file} was generated in the wrong directory ({parent_fallback}).")
            else:
                pytest.fail(f"❌ FAILED: {expected_file} was not generated at all!")

        print(f"✅ SUCCESS: {expected_file} was generated.")

        # --- Phase 2: Content Sanity Check ---
        print("\n" + "=" * 60, flush=True)
        print("🕵️  Phase 2: Content Sanity Check", flush=True)
        with open(target_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        expected_keywords = ["import", "10"]
        missing_keywords = [kw for kw in expected_keywords if kw.lower() not in content.lower()]

        if missing_keywords:
            print(f"\n--- WRITTEN FILE CONTENT START ---\n{content}\n--- WRITTEN FILE CONTENT END ---\n")
            pytest.fail(f"❌ FAILED: Script missing expected keywords: {missing_keywords}.")
        else:
            print("✅ Content sanity check passed.")

        # --- Phase 3: Execution Check ---
        print("\n" + "=" * 60, flush=True)
        print("🚀 Phase 3: Execution Check", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, expected_file],
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

            # --- NEW: Output Sanity Check ---
            stdout_lower = result.stdout.lower()
            # We expect the benchmark to print results mentioning DFS, Rule-based, and rates/percentages.
            if "dfs" not in stdout_lower or ("rate" not in stdout_lower and "%" not in stdout_lower):
                pytest.fail(
                    f"❌ FAILED: Script ran, but output indicates the benchmarks didn't actually execute (missing 'dfs' or 'rate'/'%').\nOutput was: {result.stdout.strip()}")
            else:
                print("✅ Execution check passed! Script ran successfully and produced valid benchmark output.")

        except subprocess.TimeoutExpired:
            pytest.fail("❌ FAILED: Script execution timed out (>30s).")
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
        # Step 1: Force it to explore the repo
        "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
        "Take note of all the `.py` files, especially the ones containing the agent implementations.",
        "/send",

        # Step 2a: Read relevant files
        "Now, use your `read_file` tool to inspect the main python file you just found (the main game file). "
        "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
        "/send",

        # Step 3: Draft the code IN CHAT ONLY
        "Based on the code you read across those files, write the code for `benchmark.py`. "
        "The script should do the necessary imports, instantiate the game, run 10 games for the Rule-based agent "
        "and the same number for the DFS agent, calculate their success rates, and print the results. \n\n"
        "CRITICAL: Just output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
        "/send",

        # Step 4: Write the file using the strict syntax
        "Perfect. Now use the `write_file` tool to save the code you just wrote into `benchmark.py`. "
        "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
        "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"benchmark.py\"}}</tool_call>\n"
        "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
        "/send",

        # Finish
        "Looks good, task complete.",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        expected_file="benchmark.py",
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
        # Step 1: Force it to explore the repo
        "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
        "Take note of all the `.py` files, especially the ones containing the agent implementations.",
        "/send",

        # Step 2: Read relevant files
        "Now, use your `read_file` tool to inspect the main python file you just found (the main game file). "
        "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
        "/send",

        # Step 3: Draft the code IN CHAT ONLY
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

        # Step 4: Write the file
        "Perfect. Now use the `write_file` tool to save the code you just wrote into `benchmark.py`. "
        "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
        "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"benchmark.py\"}}</tool_call>\n"
        "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
        "/send",

        # Finish
        "Looks good, task complete.",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        expected_file="benchmark.py",
        max_calls_limit=60
    )


# def test_agent_minesweeper_stats_write_script_two_phase_read_premade():
#     """
#     Works on minesweeper-solve-with-changes.zip that already has required changes in minesweeper.py.
#     Reads both the main and agent filea.
#     Keeps the agent free in choice of solution.
#     """
#     zip_target = "test_data/minesweeper-solve-with-changes.zip"
#     repo_name = "minesweeper-solve"
#
#     input_queue = [
#         # Step 1: Force it to explore the repo
#         "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
#         "Take note of all the `.py` files, especially the ones containing the agent implementations.",
#         "/send",
#
#         # Step 2a: Read relevant files
#         "Now, use your `read_file` tool to inspect the main python file you just found (the main game file). "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
#         "/send",
#
#         # Step 2b: Read relevant files
#         "Now, use your `read_file` tool to inspect further python files you just found (e.g. the agent files). "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
#         "/send",
#
#         # Step 3: Draft the code IN CHAT ONLY
#         "Based on the code you read across those files, write the code for `benchmark.py`. "
#         "The script should do the necessary imports, instantiate the game, run 10 games for the Rule-based agent "
#         "and the same number for the DFS agent, calculate their success rates, and print the results. \n\n"
#         "CRITICAL: Just output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
#         "/send",
#
#         # Step 4: Write the file using the strict syntax
#         "Perfect. Now use the `write_file` tool to save the code you just wrote into `benchmark.py`. "
#         "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
#         "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"benchmark.py\"}}</tool_call>\n"
#         "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
#         "/send",
#
#         # Finish
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
#         expected_file="benchmark.py",
#         max_calls_limit=60
#     )


# def test_agent_minesweeper_stats_write_script():
#     """
#     Tests the agent's ability to read existing files, understand their API,
#     and write a functioning, executable benchmark script.
#     """
#     zip_target = "test_data/minesweeper-solve.zip"
#     repo_name = "minesweeper-solve"
#
#     input_queue = [
#         # Step 1: Force it to explore the repo
#         "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
#         "Take note of all the `.py` files, especially the ones containing the agent implementations.",
#         "/send",
#
#         # Step 2: Read ALL relevant files
#         "Now, use your `read_file` tool to inspect ALL the python files you just found (e.g., the main game file and the agent files). "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire files. You can call the tool multiple times if needed.",
#         "/send",
#
#         # Step 3: Draft the code IN CHAT ONLY
#         "Based on the code you read across those files, write the code for `benchmark.py`. "
#         "The script should do the necessary imports, instantiate the game, run 10 games for the Rule-based agent "
#         "and 10 for the DFS agent, calculate their success rates, and print the results. \n\n"
#         "CRITICAL: Make sure you call the main loop function in agent mode, NOT interactive mode. \n\n",
#         "CRITICAL: Just output the python code in a standard ```python markdown block. DO NOT use the `write_file` tool yet.",
#         "/send",
#
#         # Step 4: Write the file using the strict syntax
#         "Perfect. Now use the `write_file` tool to save the code you just wrote into `benchmark.py`. "
#         "You MUST use this exact raw format. Do not use markdown ```json blocks:\n\n"
#         "<tool_call>{\"name\": \"write_file\", \"args\": {\"filepath\": \"benchmark.py\"}}</tool_call>\n"
#         "<payload>\n[INSERT YOUR PYTHON CODE HERE]\n</payload>",
#         "/send",
#
#         # Finish
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
#         expected_file="benchmark.py",
#         max_calls_limit=60
#     )

