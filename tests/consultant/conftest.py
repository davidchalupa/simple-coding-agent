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
        help="Specify model key from MODEL_REGISTRY (e.g. qwen2.5, hermes3)."
    )


@pytest.fixture(autouse=True)
def setup_consultant_model(request):
    selected_model = request.config.getoption("model")

    if selected_model not in MODEL_REGISTRY:
        pytest.fail(f"Model '{selected_model}' not found in MODEL_REGISTRY")

    parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
    state = ConsultantState(parsed_args)

    # Reset LLM state so llama-cpp reloads the new model handle
    state.llm = None

    fake_args = ["coding_consultant.py", "--model", selected_model]
    with patch.object(sys, "argv", fake_args):
        yield
