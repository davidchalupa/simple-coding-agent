import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test
import shutil

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def test_consultant_workflow_hello_world():
    input_queue = [
        "Write a 'Hello, World!' program in Python.",  # Prompt for the consultant to write the code
        "/send" , # Send the prompt to the consultant

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
    )
