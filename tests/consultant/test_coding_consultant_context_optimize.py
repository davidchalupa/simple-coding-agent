import os
import pytest
from tests.consultant.test_utils.test_runner import run_read_only_coding_task_test


@pytest.mark.skip(reason="Temporarily skipped")
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


@pytest.mark.skip(reason="Skipped because already implemented")
def test_consultant_workflow_kv_cache_persistence_implementation():
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

        "I would like to start the implementation of the steps above with adding KV cache persistence support. "
        "The reason is that this is the single most important feature to support more efficient working with context "
        "and memory. Please issue further tool calls if you need to gather more context to prepare for implementation. "
        "Do not write code yet, we will do that in the next step! "
        "CRITICAL: Prepare a plan for minimum viable implementation, refrain from adding functionality that is not needed.",
        "/send",

        "Now, please give me drop-in replacements in all files that are needed to make the KV cache persistence "
        "work in the minimum viable implementation.",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )


def test_consultant_workflow_kv_cache_persistence_implementation_fixes():
    input_queue = [
        "load coding_consultant.py into context.",
        "/send",

        "load the following files into context: common/llm_init.py, common/output_handler.py, common/context_hygiene_utils.py, common/guardrail_tools.py",
        "/send",

        "load also consultant/model_switcher.py into context",
        "/send",

        "load also plans/consultant_plans.md into context",
        "/send",

        "And load also plans/consultant_plans_supplement.md into context but only lines 112-137",
        "/send",

        "/mode think",
        "/send",

        # "nice, now - based on the context you have read - can you summarize the current situation we have with respect "
        # "to context management and key VRAM optimizations? focus just on summarizing the current state where we are.",
        # "/send",

        "I have done some implementation of KV cache persistence support, based on analysis done on the context you have "
        "just read above. "
        "Please issue further tool calls if you need to gather more context for how the implementation works. "
        "Only confirm briefly once done, don't write a long analysis. I'll need a fix to be done.",
        "/send",

        "Now, when I allow KV cache tracking by setting self.cache_enabled = True, I am getting the following error in logs and the consultant is rendered non-functional - how can I fix this?\n\n"
        # "[You] (Type /send to submit, /cancel to scratch draft, /undo to delete last line):\n\n"
        "💾 [KV Cache] Saved via set_cache()\n\n"
        "💾 [KV Cache] Saved context to /home/davidc/.coding_consultant/cache/qwen3.5-9b_1791567996.bin\n"
        "🧠 [Agent]: \n[Error during generation]: string indices must be integers, not 'list'.",
        "/send",

        "/quit"
    ]
    run_read_only_coding_task_test(
        input_queue=input_queue,
        working_directory=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )


@pytest.mark.skip(reason="Temporarily skipped")
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

        # "nice, now - based on the context you have read - can you summarize the current situation we have with respect "
        # "to context management and key VRAM optimizations? focus just on summarizing the current state where we are.",
        # "/send",

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
