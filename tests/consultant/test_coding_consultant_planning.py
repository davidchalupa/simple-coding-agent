import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test


def test_consultant_workflow_planning_features_conceptual_plans():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, "
        "common/guardrail_tools.py, consultant/model_switcher.py, consultant/input_and_macro_handlers.py, common/engine.py",
        "/send",

        "load the following files into context: common/guardrail_tools.py, consultant/guardrail_tools.py",
        "/send",

        "load also consultant/system_prompt_builder.py into context",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        "load also consultant/gui/consultant_worker.py and consultant/gui/main_window.py into context",
        "/send",

        "/mode think",
        "/send",

        "nice, now, I believe that our consultant plans are already quite outdated. I will need your thorough "
        "analysis of where we are with respect to the consultant development. and I would like to change focus "
        "in the plans to focus further on functionality that is capable of mitigating the inherent limited capacity "
        "of 7B/9B models. but I am not merely talking about the ability to hold longer conversation but also rather "
        "the ability to solve harder problems, with help of suitable tools. can you help me identify the right routes "
        "forward?",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )


@pytest.mark.skip(reason="Temporarily skipped")
def test_consultant_workflow_planning_features_revise():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, "
        "common/guardrail_tools.py, consultant/model_switcher.py, consultant/input_and_macro_handlers.py, common/engine.py",
        "/send",

        "load the following files into context: common/guardrail_tools.py, consultant/guardrail_tools.py",
        "/send",

        "load also consultant/system_prompt_builder.py into context",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        "load also consultant/gui/consultant_worker.py and consultant/gui/main_window.py into context",
        "/send",

        "/mode think",
        "/send",

        "nice, now, I believe that our consultant plans are already quite outdated. we now especially have the "
        "consultant gui that fulfills some of the requirements, such as code snippet copying. please first analyze "
        "what is the current situation and identify the features that have already been implemented. then revise "
        "the plans document and mention only future functionality. focus on the most impactful functionality, especially"
        "with respect to usability of 7B/9B models that have limited capacity. prioritize the functionality that could "
        "mitigate the cognitive weakness of smaller models, rather than focusing purely on usability. "
        "feel free to add also new functionality to the plan that is truly missing and would be useful. provide rationale, if so.",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )


@pytest.mark.skip(reason="Temporarily skipped")
def test_consultant_workflow_planning_features():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, "
        "common/guardrail_tools.py, consultant/model_switcher.py, consultant/input_and_macro_handlers.py",
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


@pytest.mark.skip(reason="Temporarily skipped")
def test_consultant_workflow_planning_features_auto_detect_files():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        "now, based on the main file - coding_consultant.py and the implementation plan in plans/consultant_plans.md, "
        "i'd like you to load also other files into context, based on the imports you have seen in the main file. "
        "please read only the files that are likely important for these plans. if you only need to read one function, "
        "use read_symbol tool instead of read_file.",
        "/send",

        "good. now, if there are any more files / functions / symbol that you still need to load context based on the "
        "extended reads, please do so now. if nothing else is needed, just say you are ready.",
        "/send",

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
