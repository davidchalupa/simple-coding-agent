import sys
from unittest.mock import patch
import pytest
from pathlib import Path

from model_registry import MODEL_REGISTRY
import simple_coding_agent


def pytest_addoption(parser):
    parser.addoption(
        "--model",
        action="store",
        default="qwen2.5-7b",
        help="Specify model key from MODEL_REGISTRY (e.g. qwen2.5, hermes3)."
    )
    parser.addoption(
        "--reasoning-model",
        action="store",
        default=None,
        help="Specify reasoning model key from MODEL_REGISTRY."
    )
    parser.addoption(
        "--light-system-prompt",
        action="store_true",
        help="Specify whether to use a light system prompt."
    )


@pytest.fixture(autouse=True)
def setup_agent_model(request):
    selected_model = request.config.getoption("model")
    reasoning_model = request.config.getoption("reasoning_model")
    light_system_prompt = request.config.getoption("light_system_prompt")

    # Inject active primary model config and state properties
    if selected_model in MODEL_REGISTRY:
        active_config = MODEL_REGISTRY[selected_model]
        simple_coding_agent.state.primary_model_key = selected_model
        simple_coding_agent.state.active_config = active_config
        simple_coding_agent.state.target_path = simple_coding_agent.state.models_dir / active_config["filename"]
        simple_coding_agent.state.loaded_model_name = active_config["display_name"]

    # Inject reasoning model key if provided
    if reasoning_model:
        simple_coding_agent.state.reasoning_model_key = reasoning_model

    # Reset LLM state so tests re-initialize if needed
    if hasattr(simple_coding_agent, "llm"):
        simple_coding_agent.llm = None

    # Construct mock sys.argv matching the CLI options
    fake_args = ["simple_coding_agent.py", "--model", selected_model]
    if reasoning_model:
        fake_args.extend(["--reasoning-model", reasoning_model])
    if light_system_prompt:
        fake_args.append("--light-system-prompt")

    with patch.object(sys, "argv", fake_args):
        yield
