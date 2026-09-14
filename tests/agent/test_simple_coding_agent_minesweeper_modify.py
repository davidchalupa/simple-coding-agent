import pytest

from tests.agent.test_utils.test_runner import run_automated_coding_task_test


def test_agent_minesweeper_modify_generate_only():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"

    input_queue = [
        "Read the the code in `minesweeper-solve/minesweeper.py`. ",
        "CRITICAL: Set `start_line: 1` and `max_lines: -1` so you read the entire file.",
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


def test_agent_minesweeper_modify_read_source_file_only():
    """
    Serves as a guard test for a behavior when the LLM sometimes misunderstands and just recites some Minesweeper code.
    ToDo: we need to add a check for this behavior.
    """
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"

    input_queue = [
        "Read the code in `minesweeper-solve/minesweeper.py`. "
        "CRITICAL: Set `start_line: 1` and `max_lines: -1` so you read the entire file.",
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


def test_agent_minesweeper_modify_very_detailed():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"
    new_test_file = "minesweeper-solve/test_run_game_loop.py"

    input_queue = [
        "Read the code in `minesweeper-solve/minesweeper.py`. "
        "CRITICAL: Set `start_line: 1` and `max_lines: -1` so you read the entire file.",
        "/send",

        "Good. Now I will need you to change the run_game_loop function so that it has a return value. "
        "It should return True if the game was won and otherwise it should return False. ",
        "Use the `replace_lines` tool to make this change. "
        "CRITICAL RULES:\n"
        "0. FIRST use the `read_symbol` tool with symbol_name='run_game_loop' to get its exact start_line, "
        "end_line, and the name of the next symbol in the file. Use those exact values for `replace_lines` "
        "afterward — do not count lines yourself or guess them.\n"
        "1. Identify the exact start_line and end_line of the run_game_loop function as reported by "
        "`read_symbol` (from the `def run_game_loop(...):` line down to the last line of the function body, "
        "right before the next `def`).\n"
        "2. Set `content` to the FULL rewritten function body, replacing the `break` statements in the "
        "win/loss/quit branches with `return True` or `return False` as appropriate. Keep every other line "
        "of logic unchanged.\n"
        "3. You do NOT need to retype the old code anywhere — only supply start_line, end_line, and the NEW content.\n"
        "4. Preserve the exact leading spaces (indentation) so the replaced code stays syntactically valid Python.\n"
        "5. `expected_start_snippet` MUST be the exact `def run_game_loop(...):` signature line. "
        "`expected_end_snippet` MUST be the exact signature line of the NEXT function reported by `read_symbol`. "
        "NEVER use a generic line like `while True:`, `else:`, `continue`, or a bare `if` as an anchor.\n"
        "6. Do NOT use `patch_file` for this change — it is too large for that tool. Use `replace_lines`.",
        "/send",

        # --- PHASE 2: Testing ---
        "Great. Now write a test file named `minesweeper-solve/test_run_game_loop.py` using the `unittest` framework "
        "that tests whether the run_game_loop function in `minesweeper-solve/minesweeper.py` now returns a boolean value. "
        "CRITICAL RULES FOR THE TEST:\n"
        "1. run_game_loop takes arguments (mines, counts, revealed, flags, get_action). `mines` is a set of "
        "(row, col) tuples that are actually mined — this is the ONLY thing that determines win vs loss. "
        "Do NOT attempt to monkeypatch or reassign attributes like `run_game_loop.place_mines` or "
        "`run_game_loop.compute_counts` — these do nothing, since place_mines/compute_counts are separate "
        "module-level functions, not attributes of run_game_loop, and run_game_loop never reads such attributes.\n"
        "2. For a LOSS test case: pass a `mines` set containing at least one real coordinate (e.g. {(0, 0)}), "
        "and have your `get_action` mock return a 'c' click on that exact mined coordinate, so handle_click "
        "genuinely returns False and the loss path executes.\n"
        "3. For a WIN test case: pass an empty `mines` set. Do NOT pre-fill `revealed` with all True values "
        "before calling run_game_loop — that would let the function detect a win on its very first check, "
        "without ever calling get_action or exercising handle_click, which is not a real test. Instead, start  "
        "with revealed all False, and give get_action a side_effect list using unittest.mock.MagicMock "
        "(e.g., get_action = MagicMock(side_effect=[('c', r, c) for r in range(9) for c in range(9)])). "
        "Do NOT try to use a stateful inline lambda with a for-loop, as it often causes SyntaxErrors.\n"
        "4. Use the `write_file` tool and put the full file content directly inside the JSON `content` field, "
        "properly escaped (use \\n for newlines). Do not use a `<payload>` block.\n"
        "5. `board_size` (9) and `mines_count` (12) are fixed GLOBAL constants defined in minesweeper.py — "
        "run_game_loop does NOT take board_size as a parameter and always iterates using the real global "
        "board_size (9x9). Your `counts`, `revealed`, and `flags` data structures MUST be sized as a full 9x9 "
        "grid, not a smaller board, or you will get an IndexError. Do NOT define your own local `board_size` "
        "variable with a different value.",
        "/send",

        "Run the test suite using your `run_cmd` tool. CRITICAL: You must cd minesweeper-solve first so the "
        "imports resolve correctly (e.g., cd minesweeper-solve && python -m unittest test_run_game_loop.py). "
        "If any tests fail, use your patching tools to fix the logic. If they all passed, just reply 'All good'.",
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


def test_agent_minesweeper_modify_read_function_rewrite_replace_lines():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"
    new_test_file = "minesweeper-solve/test_run_game_loop.py"

    input_queue = [
        "Use the `read_symbol` tool to load `run_game_loop` function of `minesweeper-solve/minesweeper.py` into context. "
        "CRITICAL: Remember its exact start and end lines, you will need them later.",
        "/send",

        "Good. Now I will need you to change the run_game_loop function so that it has a return value. ",
        "It should return True if the game was won and otherwise it should return False. ",
        "CRITICAL: Just output the python code in a standard ```python markdown block.",
        "/send",

        "Good. Now I need you to use the `replace_lines` tool covering the exact line range you have found before to replace the function "
        "`run_game_loop` with exactly with your updated implementation. Ensure your indentation matches the target file exactly.",
        "/send",

        # --- RETAINED PROMPT: Domain logic and testing hints kept intact ---
        "Great. Now write a test file named `minesweeper-solve/test_run_game_loop.py` using the `unittest` framework "
        "that tests whether the run_game_loop function in `minesweeper-solve/minesweeper.py` now returns a boolean value. "
        "CRITICAL RULES FOR THE TEST:\n"
        "1. run_game_loop takes arguments (mines, counts, revealed, flags, get_action). `mines` is a set of "
        "(row, col) tuples that are actually mined — this is the ONLY thing that determines win vs loss. "
        "Do NOT attempt to monkeypatch or reassign attributes like `run_game_loop.place_mines` or "
        "`run_game_loop.compute_counts` — these do nothing, since place_mines/compute_counts are separate "
        "module-level functions, not attributes of run_game_loop, and run_game_loop never reads such attributes.\n"
        "2. For a LOSS test case: pass a `mines` set containing at least one real coordinate (e.g. {(0, 0)}), "
        "and have your `get_action` mock return a 'c' click on that exact mined coordinate, so handle_click "
        "genuinely returns False and the loss path executes.\n"
        "3. For a WIN test case: pass an empty `mines` set. Do NOT pre-fill `revealed` with all True values "
        "before calling run_game_loop — that would let the function detect a win on its very first check, "
        "without ever calling get_action or exercising handle_click, which is not a real test. Instead, start  "
        "with revealed all False, and give get_action a side_effect list using unittest.mock.MagicMock "
        "(e.g., get_action = MagicMock(side_effect=[('c', r, c) for r in range(9) for c in range(9)])). "
        "Do NOT try to use a stateful inline lambda with a for-loop, as it often causes SyntaxErrors.\n"
        "4. Use the `write_file` tool and put the full file content directly inside the JSON `content` field, "
        "properly escaped (use \\n for newlines). Do not use a `<payload>` block.\n"
        "5. `board_size` (9) and `mines_count` (12) are fixed GLOBAL constants defined in minesweeper.py — "
        "run_game_loop does NOT take board_size as a parameter and always iterates using the real global "
        "board_size (9x9). Your `counts`, `revealed`, and `flags` data structures MUST be sized as a full 9x9 "
        "grid, not a smaller board, or you will get an IndexError. Do NOT define your own local `board_size` "
        "variable with a different value.",
        "/send",

        "Run the test suite using your `run_cmd` tool. CRITICAL: You must cd minesweeper-solve first so the "
        "imports resolve correctly (e.g., cd minesweeper-solve && python -m unittest test_run_game_loop.py). "
        "If any tests fail, use your patching tools to fix the logic. If they all passed, just reply 'All good'.",
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


def test_agent_minesweeper_modify_read_full_get_start_end_lines_replace_directly():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"
    new_test_file = "minesweeper-solve/test_run_game_loop.py"

    input_queue = [
        "Use the `read_file` tool to inspect the contents of `minesweeper-solve/minesweeper.py` in full.",
        "/send",

        "Good. Now call the `read_symbol` tool on `run_game_loop` to find its exact start and end lines.",
        "/send",

        # # --- ABLATED PROMPT: Tool hand-holding removed ---
        "Nice. Now I need you to change the `run_game_loop` function so that it returns True if the game was won and False otherwise. "
        "CRITICAL: use the `replace_lines` tool covering that exact line range to replace the ENTIRE function with your updated implementation. Ensure your indentation matches the original exactly.",
        "/send",

        # --- RETAINED PROMPT: Domain logic and testing hints kept intact ---
        "Great. Now write a test file named `minesweeper-solve/test_run_game_loop.py` using the `unittest` framework "
        "that tests whether the run_game_loop function in `minesweeper-solve/minesweeper.py` now returns a boolean value. "
        "CRITICAL RULES FOR THE TEST:\n"
        "1. run_game_loop takes arguments (mines, counts, revealed, flags, get_action). `mines` is a set of "
        "(row, col) tuples that are actually mined — this is the ONLY thing that determines win vs loss. "
        "Do NOT attempt to monkeypatch or reassign attributes like `run_game_loop.place_mines` or "
        "`run_game_loop.compute_counts` — these do nothing, since place_mines/compute_counts are separate "
        "module-level functions, not attributes of run_game_loop, and run_game_loop never reads such attributes.\n"
        "2. For a LOSS test case: pass a `mines` set containing at least one real coordinate (e.g. {(0, 0)}), "
        "and have your `get_action` mock return a 'c' click on that exact mined coordinate, so handle_click "
        "genuinely returns False and the loss path executes.\n"
        "3. For a WIN test case: pass an empty `mines` set. Do NOT pre-fill `revealed` with all True values "
        "before calling run_game_loop — that would let the function detect a win on its very first check, "
        "without ever calling get_action or exercising handle_click, which is not a real test. Instead, start  "
        "with revealed all False, and give get_action a side_effect list using unittest.mock.MagicMock "
        "(e.g., get_action = MagicMock(side_effect=[('c', r, c) for r in range(9) for c in range(9)])). "
        "Do NOT try to use a stateful inline lambda with a for-loop, as it often causes SyntaxErrors.\n"
        "4. Use the `write_file` tool and put the full file content directly inside the JSON `content` field, "
        "properly escaped (use \\n for newlines). Do not use a `<payload>` block.\n"
        "5. `board_size` (9) and `mines_count` (12) are fixed GLOBAL constants defined in minesweeper.py — "
        "run_game_loop does NOT take board_size as a parameter and always iterates using the real global "
        "board_size (9x9). Your `counts`, `revealed`, and `flags` data structures MUST be sized as a full 9x9 "
        "grid, not a smaller board, or you will get an IndexError. Do NOT define your own local `board_size` "
        "variable with a different value.",
        "/send",

        "Run the test suite using your `run_cmd` tool. CRITICAL: You must cd minesweeper-solve first so the "
        "imports resolve correctly (e.g., cd minesweeper-solve && python -m unittest test_run_game_loop.py). "
        "If any tests fail, use your patching tools to fix the logic. If they all passed, just reply 'All good'.",
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


def test_agent_minesweeper_modify_read_full_get_start_end_lines_rewrite_replace_lines_simple_tests():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"
    new_test_file = "minesweeper-solve/test_run_game_loop.py"

    input_queue = [
        "Use the `read_file` tool to inspect the contents of `minesweeper-solve/minesweeper.py` in full.",
        "/send",

        "Good. Now call the `read_symbol` tool on `run_game_loop` to find its exact start and end lines.",
        "/send",

        "Nice. Now I need you to change the `run_game_loop` function so that it returns True if the game was won and False otherwise. "
        "CRITICAL: use the `replace_lines` tool covering that exact line range to replace the ENTIRE function with your updated implementation. Ensure your indentation matches the original exactly.",
        "/send",

        "Write a test file `minesweeper-solve/test_run_game_loop.py` using `unittest` that checks if `run_game_loop` returns"
        "a boolean for win and loss outcomes.",
        "/send",

        "Run the new tests using `run_cmd` and fix any errors if they fail. CRITICAL: You must cd to the same directory "
        "where `minesweeper-solve/minesweeper.py` is, for the imports to resolve correctly (e.g., cd minesweeper-solve && python -m unittest test_run_game_loop.py).",
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


@pytest.mark.skip(reason="Too simplified for the agent yet")
def test_agent_minesweeper_modify_simplified():
    target_file = "minesweeper-solve/minesweeper.py"
    zip_source = "test_data/minesweeper-solve.zip"
    new_test_file = "minesweeper-solve/test_run_game_loop.py"

    input_queue = [
        "Read `minesweeper-solve/minesweeper.py` to understand the code.",
        "/send",

        "Modify the `run_game_loop` function so that it returns `True` if the game is won and `False` otherwise. Use `replace_lines` to update the function.",
        "/send",

        "Write a test file `minesweeper-solve/test_run_game_loop.py` using `unittest` that checks if `run_game_loop` returns a boolean"
        "for win and loss outcomes.",
        "/send",

        "Run the new tests using `run_cmd` and fix any errors if they fail.",
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
