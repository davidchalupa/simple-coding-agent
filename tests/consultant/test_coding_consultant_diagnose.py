import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test
import shutil

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def test_consultant_workflow_consultant_agent_diagnose():
    input_queue = [
        "Load `coding_consultant.py` into context.",
        "/send",

        "Load `simple_coding_agent.py` into context.",
        "/send",

        "/diagnose Now, can you give me a comparison of these two files you have just read and pinpoint the similarities and differences? ",
        "Would you recommend refactoring them or rather keeping them separate?",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=PROJECT_ROOT,
    )
