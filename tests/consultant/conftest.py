import sys
from unittest.mock import patch
import pytest

from model_registry import MODEL_REGISTRY
from coding_consultant import ConsultantState, parse_cli_arguments


def pytest_addoption(parser):
    parser.addoption(
        "--model",
        action="store",
        default="qwen2.5-7b",
        help="Specify model key from MODEL_REGISTRY (e.g. qwen2.5-7b)."
    )
    parser.addoption(
        "--reasoning-model",
        action="store",
        default="qwen2.5-7b",
        help="Specify model key from MODEL_REGISTRY (e.g. deepseek-r1-distill-qwen-7b)."
    )


@pytest.fixture(autouse=True)
def setup_consultant_model(request):
    selected_model = request.config.getoption("model")
    selected_reasoning_model = request.config.getoption("reasoning_model")

    if selected_model not in MODEL_REGISTRY:
        pytest.fail(f"Model '{selected_model}' not found in MODEL_REGISTRY")
    if selected_reasoning_model not in MODEL_REGISTRY:
        pytest.fail(f"Model '{selected_reasoning_model}' not found in MODEL_REGISTRY")

    parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
    state = ConsultantState(parsed_args)

    # Reset LLM state so llama-cpp reloads the new model handle
    state.llm = None

    fake_args = ["coding_consultant.py", "--model", selected_model, "-reasoning--model", selected_reasoning_model]
    with patch.object(sys, "argv", fake_args):
        yield
