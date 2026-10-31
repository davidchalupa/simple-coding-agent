import pytest
import shutil
import os

from tests.agent.test_utils.test_runner import run_automated_coding_task_test

# Project root directory (parent of the tests/ folder)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def prepare_self_repo_sandbox(target_dir):
    """Copies the actual repository into a sandbox, ignoring cache and git history."""
    ignore_patterns = shutil.ignore_patterns(
        ".git", ".venv", ".venv*", "venv", "env", "__pycache__",
        ".idea", ".vscode", "dist", "build", ".pytest_cache", "models",
        "README.md", "*.zip"
    )
    repo_dest = os.path.join(target_dir, "simple-coding-agent")
    shutil.copytree(PROJECT_ROOT, repo_dest, ignore=ignore_patterns)


def test_agent_load_file_into_context():
    def check_regurgitation(stdout: str):
        """
        We must verify the agent didn't just dump code.
        If the agent's output contained a markdown block with 'class Minesweeper', it failed to follow instructions properly.
        """
        if "```python" in stdout:
            raise AssertionError("FAIL: Agent regurgitated source code instead of performing the requested action.")

    """
    Serves as a guard test for a behavior when the LLM sometimes misunderstands and just recites some Minesweeper code.
    """
    target_file = "coding_consultant.py"

    input_queue = [
        "Load `coding_consultant.py` into context. ",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=None,  # We are hooking into the directory copy directly!
        repo_name="simple-coding-agent",
        setup_sandbox_hook=prepare_self_repo_sandbox,
        target_file_path=target_file,
        max_calls_limit=30,
        agent_output_validator=check_regurgitation,
    )
