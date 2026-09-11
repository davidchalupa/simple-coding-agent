import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test

# Resolve the absolute path to the zip file containing the frozen code state
ZIP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_data", "simple-coding-agent.zip"))

def test_consultant_workflow_gather_context_only():
    input_queue = [
        "Load `main()` function from `coding_consultant.py` into context.",
        "/send",

        "Load `ConsultantState` from `coding_consultant.py` into context.",
        "/send",

        "Load `tests/consultant/test_utils/test_runner.py` into context.",
        "/send",

        "Load `tests/consultant/test_coding_consultant_load_files_refactor.py` into context.",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        zip_file_path=ZIP_PATH,
        # repo_name removed so it defaults to the root of the extracted zip
    )


def test_consultant_workflow_gather_leaking_state_without_diagnose():
    input_queue = [
        "Load `main()` function from `coding_consultant.py` into context.",
        "/send",

        "Load `ConsultantState` from `coding_consultant.py` into context.",
        "/send",

        "Load `tests/consultant/test_utils/test_runner.py` into context.",
        "/send",

        "Load `tests/consultant/test_coding_consultant_load_files_refactor.py` into context.",
        "/send",

        "Now, I will need your help. The above is my coding consultant and the test runner that are used in my tests. Every test uses test runner separately but some read ",
        "the same files again. And the consult cache seems to bleed from one test case to another. Evidence from logs (below is snippet from the second test that ran):",
        '[Agent]: ```json',
        '{"name": "read_file", "args": {"filepath": "coding_consultant.py", "start_line": 1, "max_lines": -1}}',
        '```',
        "📎 [Consult Cache] Reusing cached result for read_file on /home/davidc/src/simple-coding-agent/coding_consultant.py. ",
        "I suspect that consult cache is not properly cleaned by the test runner. Can you diagnose and show me how to fix this?",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        zip_file_path=ZIP_PATH,
    )


def test_consultant_workflow_leaking_state_diagnose():
    input_queue = [
        "Load `main()` function from `coding_consultant.py` into context.",
        "/send",

        "Load `ConsultantState` from `coding_consultant.py` into context.",
        "/send",

        "Load `tests/consultant/test_utils/test_runner.py` into context.",
        "/send",

        "Load `tests/consultant/test_coding_consultant_load_files_refactor.py` into context.",
        "/send",

        "/diagnose "
        "Now, I will need your help. The above is my coding consultant and the test runner that are used in my tests. Every test uses test runner separately but some read ",
        "the same files again. And the consult cache seems to bleed from one test case to another. Evidence from logs (below is snippet from the second test that ran):",
        '[Agent]: ```json',
        '{"name": "read_file", "args": {"filepath": "coding_consultant.py", "start_line": 1, "max_lines": -1}}',
        '```',
        "📎 [Consult Cache] Reusing cached result for read_file on /home/davidc/src/simple-coding-agent/coding_consultant.py. ",
        "I suspect that consult cache is not properly cleaned by the test runner. Can you diagnose and show me how to fix this?",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        zip_file_path=ZIP_PATH,
    )
