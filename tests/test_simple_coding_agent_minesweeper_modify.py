from tests.test_utils.test_runner import run_automated_coding_task_test



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
