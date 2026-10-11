import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test


def test_consultant_workflow_planning_features():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, "
        "common/guardrail_tools.py, consultant/model_switcher.py",
        "/send",

        "load the following files into context: common/guardrail_tools.py, consultant/guardrail_tools.py",
        "/send",

        "load also consultant/system_prompt_builder.py into context",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        # "And load also plans/consultant_plans_supplement.md into context",
        # "/send",

        "/mode think",
        "/send",

        "nice, now - based on the context you have read - can you summarize the current situation we have with respect "
        "to features that we have outlined in the plans documents? pinpoint the next most important feature from the plan you would "
        "recommend implementing and provide a brief explanation for your choice.",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
