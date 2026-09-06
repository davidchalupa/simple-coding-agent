from tests.test_utils.test_runner import run_automated_coding_task_test


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


def test_agent_minesweeper_modify_with_replace_lines():
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

# patch_file based version - currently too unreliable
# def test_agent_minesweeper_modify_with_patch_file():
#     target_file = "minesweeper-solve/minesweeper.py"
#     zip_source = "test_data/minesweeper-solve.zip"
#     new_test_file = "minesweeper-solve/test_run_game_loop.py"
#
#     input_queue = [
#         "Read the code in `minesweeper-solve/minesweeper.py`. "
#         "CRITICAL: Set `start_line: 1` and `max_lines: 1000` for each tool call so you read the entire file.",
#         "/send",
#
#         "Good. Now I will need you to change the run_game_loop function so that it has a return value. "
#         "It should return True if the game was won and otherwise it should return False. ",
#         "Use the `patch_file` tool to make this change with THREE SEPARATE small, targeted edits — "
#         "one for each `break` statement inside run_game_loop. Do NOT try to replace the whole function "
#         "body in one call.\n"
#         "CRITICAL RULES FOR PATCHING:\n"
#         "1. Make three separate `patch_file` calls, one per `break` statement in run_game_loop:\n"
#         "   a) The `break` right after the win-condition print statements -> replace with `return True`.\n"
#         "   b) The `break` right after \"Quitting. Bye!\" -> replace with `return False`.\n"
#         "   c) The `break` right after the mine-hit / BOOM print statements -> replace with `return False`.\n"
#         "2. CONTEXT RULE: For each `old_content`, include the `break` line PLUS at least 2-3 lines of the "
#         "surrounding code (e.g. the preceding print statement(s)) so the match is unique in the file. "
#         "Do NOT just put 'break' alone — patch_file requires at least 3 lines of context and will reject "
#         "shorter snippets.\n"
#         "3. You MUST preserve the exact leading spaces (indentation) in both `old_content` and `new_content` "
#         "to prevent Python IndentationErrors.\n"
#         "4. ANTI-LOOP RULE: If `patch_file` fails to find your `old_content`, DO NOT blindly add more lines "
#         "of context. Instead, use the `read_file` tool again to verify the exact text, spaces, and newlines, "
#         "then retry with the corrected snippet.\n"
#         "5. Make the three patches ONE AT A TIME, in separate tool calls, checking the result of each before "
#         "moving to the next.",
#         "/send",
#
#         # --- PHASE 2: Testing ---
#         "Great. Now write a test file named `minesweeper-solve/test_run_game_loop.py` using the `unittest` framework "
#         "that tests whether the run_game_loop function in `minesweeper-solve/minesweeper.py` now returns a boolean value. "
#         "CRITICAL: Use the `write_file` tool and put the full file content directly inside the JSON `content` field, "
#         "properly escaped (use \\n for newlines). Do not use a `<payload>` block.",
#         "/send",
#
#         "Run the test suite using your `run_cmd` tool. If any tests fail, use your patching tools to fix the logic. "
#         "If they all passed, just reply 'All good'.",
#         "/send",
#
#         "/quit"
#     ]
#
#     run_automated_coding_task_test(
#         input_queue=input_queue,
#         zip_file_path=zip_source,
#         repo_name="",
#         target_file_path=target_file,
#         check_for_change=True,
#         expected_new_files=[new_test_file],
#         run_unittest_file=new_test_file,
#         max_calls_limit=30
#     )
