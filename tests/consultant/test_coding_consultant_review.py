import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test


def test_consultant_workflow_review_flow_diagram_agent():
    input_queue = [
        "load simple_coding_agent.py into context.",
        "/send",

        "load also docs/flow_diagram_agent.txt into context",
        "/send",

        "/mode think",
        "/send",

        "can you write a brief review for the ascii art-style flow diagram you have read for the simple coding agent? "
        "is it complete and up-to-date? or is there anything stale? would you recommend having it committed in the "
        "repository of the project?",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
