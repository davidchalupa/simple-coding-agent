import sys
from unittest.mock import patch
import pytest

from model_registry import MODEL_REGISTRY

import simple_coding_agent


def pytest_addoption(parser):
    parser.addoption(
        "--model",
        action="store",
        default="qwen2.5-7b",
        help="Specify model key from MODEL_REGISTRY (e.g. qwen2.5, hermes3)."
    )


@pytest.fixture(autouse=True)
def setup_agent_model(request):
    selected_model = request.config.getoption("model")

    # Inject active model config into simple_coding_agent
    if selected_model in MODEL_REGISTRY:
        active_config = MODEL_REGISTRY[selected_model]
        simple_coding_agent.state.target_path = simple_coding_agent.state.target_path
        simple_coding_agent.state.loaded_model_name = active_config["display_name"]
        simple_coding_agent.state.active_config = active_config

    # Reset LLM state so llama-cpp reloads the new model handle
    simple_coding_agent.llm = None

    fake_args = ["simple_coding_agent.py", "--model", selected_model]
    with patch.object(sys, "argv", fake_args):
        yield
