import os
import shutil

import pytest

from tests.test_utils.test_runner import run_automated_coding_task_test


# Project root directory (parent of the tests/ folder)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TESTS_DIR = os.path.dirname(__file__)


def resolve_zip_path(zip_file_path):
    """Finds the zip file across project root and tests directory candidate locations."""
    candidates = [
        os.path.abspath(os.path.join(PROJECT_ROOT, zip_file_path)),
        os.path.abspath(os.path.join(TESTS_DIR, zip_file_path)),
        os.path.abspath(os.path.join(PROJECT_ROOT, "tests", zip_file_path)),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]  # Fallback to primary for error logging


def validate_minesweeper_readme(file_path):
    """Custom file validation for the minesweeper /readme macro tests."""
    file_size = os.path.getsize(file_path)
    if file_size <= 50:
        pytest.fail("README.md was generated but appears empty or too short!")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "minesweeper" in content.lower():
        print("✅ Contextual check passed (Found target keyword).")
    else:
        print("⚠️ NOTICE: The word 'Minesweeper' wasn't in the README, check the prompt tuning.")


def test_agent_readme_generation():
    """Tests the /readme macro by extracting a sample repo and running the agent on it."""
    zip_target = resolve_zip_path("test_data/minesweeper-solve.zip")
    repo_name = "minesweeper-solve"
    input_queue = [
        "/readme .",
        "/send",
        "Looks good, task complete.",
        "/send",
        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        expected_file="README.md",
        max_calls_limit=40,
        custom_file_validator=validate_minesweeper_readme
    )


def test_agent_readme_generation_deep():
    """Tests the /readme macro with deep generation flag."""
    zip_target = resolve_zip_path("test_data/minesweeper-solve.zip")
    repo_name = "minesweeper-solve"
    input_queue = [
        "/readme --deep .",
        "/send",
        "Looks good, task complete.",
        "/send",
        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        expected_file="README.md",
        max_calls_limit=40,
        custom_file_validator=validate_minesweeper_readme
    )


def prepare_self_repo_sandbox(target_dir):
    """Copies the actual repository into a sandbox, ignoring cache and git history."""
    ignore_patterns = shutil.ignore_patterns(
        ".git", ".venv", ".venv*", "venv", "env", "__pycache__",
        ".idea", ".vscode", "dist", "build", ".pytest_cache", "models",
        "README.md", "*.zip"
    )
    repo_dest = os.path.join(target_dir, "simple-coding-agent")
    shutil.copytree(PROJECT_ROOT, repo_dest, ignore=ignore_patterns)


def validate_self_readme(file_path):
    """Custom file validation to ensure the self-repo readmes are meaty enough."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    word_count = len(content.split())
    print(f"📊 Self-README Word Count: {word_count} words", flush=True)

    if word_count < 150:
        pytest.fail(f"README is too shallow ({word_count} words).")


def test_self_readme_generation_deep():
    """Tests /readme --deep against the current live state of the agent codebase."""
    input_queue = [
        "/readme --deep .", "/send",
        "Looks good, task complete.", "/send", "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=None,  # We are hooking into the directory copy directly!
        repo_name="simple-coding-agent",
        setup_sandbox_hook=prepare_self_repo_sandbox,
        expected_file="README.md",
        expected_keywords=["agent"],
        max_calls_limit=40,
        custom_file_validator=validate_self_readme
    )


def test_self_readme_generation_deep_ast():
    """Tests /readme --deep-ast against the current live state of the agent codebase."""
    input_queue = [
        "/readme --deep-ast .", "/send",
        "Looks good, task complete.", "/send", "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=None,
        repo_name="simple-coding-agent",
        setup_sandbox_hook=prepare_self_repo_sandbox,
        expected_file="README.md",
        expected_keywords=["agent"],
        max_calls_limit=40,
        custom_file_validator=validate_self_readme
    )
