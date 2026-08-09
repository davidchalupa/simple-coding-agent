import sys
from unittest.mock import patch
import pytest

import simple_coding_agent


def pytest_addoption(parser):
    parser.addoption(
        "--hermes",
        action="store_true",
        default=False,
        help="Run tests using the Hermes 3 model."
    )
    parser.addoption(
        "--model",
        action="store",
        default="qwen2.5",
        help="Specify model key from MODEL_REGISTRY (e.g. qwen2.5, hermes3)."
    )


@pytest.fixture(autouse=True)
def setup_agent_model(request):
    # Check if --hermes flag was passed, otherwise read --model option
    if request.config.getoption("hermes"):
        selected_model = "hermes3"
    else:
        selected_model = request.config.getoption("model")

    # Inject active model config into simple_coding_agent
    if selected_model in simple_coding_agent.MODEL_REGISTRY:
        active_config = simple_coding_agent.MODEL_REGISTRY[selected_model]
        simple_coding_agent.target_path = simple_coding_agent.script_dir / "models" / active_config["filename"]
        simple_coding_agent.loaded_model_name = active_config["display_name"]
        simple_coding_agent.active_config = active_config

    # Reset LLM state so llama-cpp reloads the new model handle
    simple_coding_agent.llm = None

    fake_args = ["simple_coding_agent.py", "--model", selected_model]
    with patch.object(sys, "argv", fake_args):
        yield
