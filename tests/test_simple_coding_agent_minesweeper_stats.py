import re
import pytest

from tests.test_utils.test_runner import run_automated_coding_task_test


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
        run_script_file="benchmark.py",
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
        run_script_file="benchmark.py",
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

