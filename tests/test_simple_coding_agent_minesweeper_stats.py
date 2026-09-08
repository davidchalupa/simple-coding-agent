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

    has_rate_language = "rate" in stdout_lower or "%" in stdout_lower
    has_fraction_language = bool(re.search(r'\b\d+\s*/\s*10\b', stdout))

    if "dfs" not in stdout_lower or not (has_rate_language or has_fraction_language):
        pytest.fail(
            f"❌ FAILED: Script ran, but output indicates benchmarks didn't execute "
            f"(missing 'dfs' or any rate/'%'/'X/10' indicator).\nOutput was: {stdout.strip()}"
        )

    # If the script printed rate/percentage language, parse and sanity-check those numbers.
    rates = re.findall(r'rate.*?:\s*(\d+(?:\.\d+)?)', stdout_lower)
    if rates:
        all_parsed = [float(r) for r in rates]
        if all(r == 0.0 for r in all_parsed):
            pytest.fail(
                f"❌ FAILED: Success rates are all 0.0. The agent likely broke the game loop or ran on an empty board.\nSTDOUT:\n{stdout}"
            )

    # Behavioral check that games were actually run against a denominator of 10.
    denominator_matches = re.findall(r'\b(\d+)\s*/\s*10\b', stdout)
    if not denominator_matches:
        pytest.fail(
            f"❌ FAILED: Could not confirm 10 games were run per agent (expected an 'X/10' style count in output).\nSTDOUT:\n{stdout}"
        )

    if all(int(n) == 0 for n in denominator_matches):
        pytest.fail(
            f"❌ FAILED: All parsed win counts are 0. The agent likely broke the game loop or ran on an empty board.\nSTDOUT:\n{stdout}"
        )

    print(f"✅ Success rate validation passed! Game counts: {denominator_matches}"
          + (f", parsed rates: {all_parsed}" if rates else ""))


def test_agent_minesweeper_stats_write_script_read_main_only_premade_minimal():
    """
    Works on minesweeper-solve-with-changes.zip that already has required changes in minesweeper.py.
    Intentionally reads only the main file.
    Keeps the agent free in choice of solution, but explicitly requires minimal code and imports.
    """
    zip_target = "test_data/minesweeper-solve-with-changes.zip"
    repo_name = "minesweeper-solve"

    input_queue = [
        "Use the `list_tree` (or equivalent) tool to view the files in this directory. "
        "Take note of all the `.py` files, especially the ones containing the agent implementations.",
        "/send",

        "Now, use your `read_file` tool to inspect the main python file you just found (the main game file). "
        "CRITICAL: Set `start_line: 1` and `max_lines: -1` so you read the entire file.",
        "/send",

        "Based on the code you read, write the code for `benchmark.py`. "
        "CRITICAL CONSTRAINTS:\n"
        "1. Be extremely minimalistic.\n"
        "2. DO NOT redefine or rewrite any classes or functions from the existing files.\n"
        "3. Import every module and function actually used by the generated benchmark.\n"
        "4. You MUST import the game engine functions needed from `minesweeper.py`, including "
        "`place_mines`, `compute_counts`, `handle_click`, and `run_game_loop` as applicable.\n"
        "5. You MUST import `ai_get_action` and `dfs_get_action` from `action_ai_agent.py`.\n"
        "6. DO NOT use the `main_*` launcher functions as agent callbacks. Pass the actual `get_action` callbacks "
        "to the game loop.\n"
        "7. DO NOT use `argparse` or require command line arguments.\n"
        "8. When executed directly, automatically run exactly 10 games for the Rule-based agent and exactly 10 "
        "games for the DFS agent sequentially.\n"
        "9. Calculate and print their success rates, including result for each agent STRICTLY in format 'X/10 games won' (e.g. '7/10 games won').\n"
        "10. DO NOT import or reference interactive-mode functions such as `interactive_get_action`, "
        "`main_interactive`, or `prompt_first_click`.\n"
        "11. Every name used by the generated script must either be defined in the script or explicitly imported.\n"
        "12. Compute the FIRST click's row and column ONCE (e.g. `first_r, first_c = random.randrange(board_size), "
        "random.randrange(board_size)`), and pass that SAME (first_r, first_c) pair to BOTH `place_mines` and "
        "`handle_click`. Do NOT call random.randrange separately for each function — that would let the first "
        "click land on a different cell than the one place_mines was told to keep safe."
        "\n"
        "Just output the python code in a standard ```python markdown block. "
        "DO NOT use the `write_file` tool yet.",
        "/send",

        "Now save the code you just generated into `benchmark.py` using the `write_file` tool. "
        "Do not output the code again. "
        "Do not use a <payload> block. "
        "Use the normal write_file JSON format with the complete code in the `content` field.",
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
