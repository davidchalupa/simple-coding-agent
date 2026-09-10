import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test
import shutil

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def test_consultant_workflow_load_files_into_context():
    input_queue = [
        "Load `coding_consultant.py` into context.",
        "/send",

        "Load `common/llm_init.py` into context.",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=PROJECT_ROOT,
    )


def test_consultant_workflow_refactor_test_runner():
    input_queue = [
        "Load `tests/consultant/test_utils/test_runner.py` into context.",
        "/send",

        "I will need you to identify whether there are any unused function arguments in this file. Are there any?",
        "/send",

        "Good. Now I need you to refactor this file. If there are any unused function arguments in this "
        "file, then give me a refactored version of it that will get rid ",
        "of the unused function arguments. Make sure that the functionality stays intact!",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=PROJECT_ROOT,
    )
