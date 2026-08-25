import re
import pytest

from tests.test_utils.test_runner import run_automated_coding_task_test


def check_benchmark_success_rates(stdout: str):
    """
    Validation function for the benchmark script output.
    Ensures benchmark text is printed, win rates are parsed and non-zero,
    and confirms games were actually run against a denominator of 10.
    """
    stdout_lower = stdout.lower()

    if "dfs" not in stdout_lower or ("rate" not in stdout_lower and "%" not in stdout_lower):
        pytest.fail(
            f"❌ FAILED: Script ran, but output indicates benchmarks didn't execute (missing 'dfs' or 'rate'/'%').\nOutput was: {stdout.strip()}"
        )

    # NEW REGEX: Looks for 'rate', followed by ANY characters (like " for dfs_agent"),
    # then a colon, optional spaces, and the number.
    rates = re.findall(r'rate.*?:\s*(\d+(?:\.\d+)?)', stdout_lower)

    if not rates:
        # HARD FAIL: Do not allow the test to pass if we can't find the numbers.
        pytest.fail(
            f"❌ FAILED: Could not explicitly parse rates from output. Check regex or agent script output.\nSTDOUT:\n{stdout}"
        )

    all_parsed = [float(r) for r in rates]

    if all(r == 0.0 for r in all_parsed):
        pytest.fail(
            f"❌ FAILED: Success rates are all 0.0. The agent likely broke the game loop or ran on an empty board.\nSTDOUT:\n{stdout}"
        )

    # Behavioral check that games were actually run against a denominator of 10,
    # replacing the old brittle static source-text "10" keyword check.
    denominator_matches = re.findall(r'\b(\d+)\s*/\s*10\b', stdout)
    if not denominator_matches:
        pytest.fail(
            f"❌ FAILED: Could not confirm 10 games were run per agent (expected an 'X/10' style count in output).\nSTDOUT:\n{stdout}"
        )

    print(f"✅ Success rate validation passed! Parsed rates: {all_parsed}, game counts: {denominator_matches}")


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
        "and the same number for the DFS agent, calculate their success rates, and print the results, including "
        "how many games out of the total were won for each agent in an 'X/10' style format (e.g. '7/10 games won'). \n\n"
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
        expected_keywords=["import"],
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
        "6. Calculate their success rates and print the results to standard output, including how many games out of "
        "the total were won for each agent in an 'X/10' style format (e.g. '7/10 games won').\n"
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
        expected_keywords=["import"],
        custom_output_validator=check_benchmark_success_rates,
        max_calls_limit=60
    )


# def test_agent_minesweeper_stats_modify_and_write_script():
#     """
#     Combined multi-phase test optimized for Qwen 2.5 8B:
#     Phase 1: Patches run_game_loop in minesweeper.py to return True/False.
#     Phase 2: Inspects agent implementations and writes benchmark.py.
#     """
#     zip_source = "test_data/minesweeper-solve.zip"
#     repo_name = ""
#     target_file = "minesweeper-solve/minesweeper.py"
#     new_script_file = "minesweeper-solve/benchmark.py"
#
#     input_queue = [
#         # --- PHASE 1: Modify run_game_loop ---
#         "Read the code in `minesweeper-solve/minesweeper.py`. "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file.",
#         "/send",
#
#         "Good. Now change the `run_game_loop` function in `minesweeper-solve/minesweeper.py` so that it returns a boolean.\n"
#         "Replace the `break` statements for each of the 3 conditions using `patch_file`:\n"
#         "1. Win condition (`revealed_count >= total_to_reveal`): replace `break` with `return True`\n"
#         "2. Loss condition (`BOOM — you clicked a mine`): replace `break` with `return False`\n"
#         "3. Quit condition (`action == 'q'`): replace `break` with `return False`\n\n"
#         "CRITICAL RULES FOR PATCHING:\n"
#         "- CONTEXT RULE: Include at least 3 surrounding lines in `old_content` to uniquely identify each spot.\n"
#         "- INDENTATION RULE: Preserve exact leading spaces in `old_content` and `new_content`.\n"
#         "- TERMINATION RULE: Once all 3 patches are applied, stop calling tools and reply 'Patches applied successfully'.",
#         "/send",
#
#         # --- PHASE 2: Inspect workspace and generate benchmark script ---
#         "Use the `list_tree` tool to view files in the `minesweeper-solve` directory. "
#         "Take note of all `.py` files containing agent implementations.",
#         "/send",
#
#         "Now use `read_file` to inspect `minesweeper-solve/minesweeper.py` and `minesweeper-solve/action_ai_agent.py`. "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call.",
#         "/send",
#
#         "Based on the code read, write the code for `benchmark.py`.\n"
#         "CRITICAL CONSTRAINTS:\n"
#         "1. Be minimalistic and do NOT rewrite existing functions or classes.\n"
#         "2. Imports: You MUST import `place_mines`, `compute_counts`, `handle_click`, `run_game_loop` from `minesweeper.py` and `ai_get_action`, `dfs_get_action` from `action_ai_agent.py`.\n"
#         "3. Do NOT use `main_*` launcher functions. Pass actual `get_action` callbacks to `run_game_loop`.\n"
#         "4. Automatically run 10 games for Rule-based agent and 10 games for DFS agent when executed directly.\n"
#         "5. Calculate success rates and print results to standard output.\n\n"
#         "Just output the python code in a standard ```python markdown block. DO NOT use tools yet.",
#         "/send",
#
#         "Now use the `write_file` tool to save the python code into `minesweeper-solve/benchmark.py`.\n"
#         "CRITICAL JSON RULE: To avoid escaping issues, use SINGLE QUOTES (`'`) for all strings in your Python code (e.g., `if __name__ == '__main__':` and `print(f'{agent_name}...')`). Do not use literal backslashes (`\\`) to escape double quotes.\n"
#         "Pass `filepath: \"minesweeper-solve/benchmark.py\"` and put the full python code inside the `content` argument.",
#         "/send",
#         "Looks good, task complete.",
#         "/send",
#
#         "/quit"
#     ]
#
#     run_automated_coding_task_test(
#         input_queue=input_queue,
#         zip_file_path=zip_source,
#         repo_name=repo_name,
#         target_file_path=target_file,
#         check_for_change=True,
#         expected_new_files=[new_script_file],
#         run_script_file=new_script_file,
#         expected_keywords=["import", "10"],
#         custom_output_validator=check_benchmark_success_rates,
#         max_calls_limit=40
#     )

