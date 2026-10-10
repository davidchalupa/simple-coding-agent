import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test


def test_consultant_workflow_context_optimize_analysis():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, common/guardrail_tools.py",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        "And load also plans/consultant_plans_supplement.md into context but only lines 112-137",
        "/send",

        "/mode think",
        "/send",

        "nice, now - based on the context you have read - can you summarize the current situation we have with respect "
        "to context management and key VRAM optimizations? focus just on summarizing the current state where we are.",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )


@pytest.mark.skip(reason="Temporarily skipped")
def test_consultant_workflow_context_optimize_implement():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, common/guardrail_tools.py",
        "/send",

        "load also consultant/system_prompt_builder.py into context",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        "And load also plans/consultant_plans_supplement.md into context but only lines 112-137",
        "/send",

        "/mode think",
        "/send",

        """
        Now, as I understand, with respect to context hygiene - Output token enforcements is not implemented yet.
        Model can generate long responses regardless of VRAM constraints.
        Though we do have some limit in output_handler - do I understand correctly that the 4096 is the current
        limit?

        for chunk in llm.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=temperature,
                repeat_penalty=repeat_penalty,
                max_tokens=4096,
                stop=stop or []
        ):

        If so, how can we set max_tokens smarter? Or shall we try to enforce the constraints with a minor system prompt
        change? Or we try to do it somehow programatically?
        Remember, different types of prompts will likely require different token limits.
        And we even have both coding and reasoning modes - and the reasoning model may require more generous token
        limit for a good answer.
        """,
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )


def test_consultant_workflow_kv_cache_persistence_implementation_code_review():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, common/guardrail_tools.py, consultant/model_switcher.py",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        "And load also plans/consultant_plans_supplement.md into context but only lines 112-137",
        "/send",

        "/mode think",
        "/send",

        "I have done some implementation of KV cache persistence support, based on analysis done on the context you have "
        "just read above. "
        "Please issue further tool calls if you need to gather more context for how the implementation works. "
        "Do not yet write a code review, just state how KV cache persistence is implemented in the consultant.",
        "/send",

        "Now, please give me a comprehensive but brief code review of the KV cache persistence approach. "
        "Is the implementation correct? Does it do what it should do? What is the impact on context and resource "
        "management? Will it help save context and VRAM?",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
