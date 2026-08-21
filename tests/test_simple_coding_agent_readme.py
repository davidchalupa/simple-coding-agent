import os
import tempfile
import shutil
import zipfile
from unittest.mock import patch
import pytest

import simple_coding_agent

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


def run_automated_readme_test(input_queue, zip_file_path, repo_name, max_calls_limit=30):
    """
    Custom runner for the /readme macro that extracts a zipped repository
    into a temporary sandbox, allowing the agent to analyze it and write a README.
    """
    print("🧪 Starting Automated Agent Flow Test for /readme...", flush=True)

    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix="agent_readme_sandbox_")
    print(f"📁 Created temporary sandbox at: {test_sandbox}", flush=True)

    # --- Extract the ZIP file into the sandbox ---
    source_zip_path = resolve_zip_path(zip_file_path)
    if not os.path.exists(source_zip_path):
        shutil.rmtree(test_sandbox)
        pytest.fail(f"Real target zip file not found at: {source_zip_path}")

    with zipfile.ZipFile(source_zip_path, 'r') as zip_ref:
        zip_ref.extractall(test_sandbox)

    print(f"🌱 Extracted {zip_file_path} into sandbox.", flush=True)

    # --- Direct State Injection ---
    simple_coding_agent.session_cwd = test_sandbox
    simple_coding_agent.FORCE_TESTING = True

    safety_counter = {"calls": 0, "max_calls": max_calls_limit}

    def smart_input_mocker(prompt=""):
        safety_counter["calls"] += 1
        if safety_counter["calls"] > safety_counter["max_calls"]:
            print("\n🛑 [Test Overload] Too many input calls. Forcing exit.", flush=True)
            return "/quit"

        prompt_str = str(prompt).lower()

        # Catch tool approvals (e.g., allowing write_file for README.md)
        if "allow" in prompt_str or "y/n" in prompt_str:
            print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
            return "y"

        # Consume from the queue
        if input_queue:
            next_input = input_queue.pop(0)
            print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
            return next_input

        # --- Error recovery fallback ---
        # If the queue is empty but the agent is reporting a JSON/tool error, tell it to fix it!
        if "error" in prompt_str or "json" in prompt_str or "failed" in prompt_str:
            print("\n⚠️ [Automated Test] Agent hit an error (likely truncated JSON). Triggering retry.", flush=True)
            return "Your output was truncated or invalid. Please write the file again, but keep it brief and DO NOT repeat lines."

        # Default fallback if queue is exhausted but agent is still prompting
        return "/quit"

    try:
        # Move agent execution into the sandbox
        os.chdir(test_sandbox)

        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                simple_coding_agent.main()
            except SystemExit as e:
                print(f"\n🏁 Agent terminated with code: {e.code}", flush=True)

        # --- Phase 1: Generation Results ---
        print("\n" + "=" * 60, flush=True)
        print("📊 Phase 1: Readme Generation Verification", flush=True)

        # Ensure the agent generated the README.md in the current directory
        readme_path = os.path.join(test_sandbox, repo_name, "README.md")

        if os.path.exists(readme_path):
            print("✅ SUCCESS: README.md was generated.", flush=True)

            # --- Phase 2: Content Sanity Check ---
            print("\n" + "=" * 60, flush=True)
            print("🕵️  Phase 2: Content Sanity Check", flush=True)

            file_size = os.path.getsize(readme_path)
            with open(readme_path, "r", encoding="utf-8") as f:
                content = f.read()

            print(f"README Size: {file_size} bytes", flush=True)

            if file_size > 50:  # Basic check to ensure it's not empty or just a title
                print("✅ Content length check passed.")
            else:
                pytest.fail("README.md was generated but appears empty or too short!")

            # Optional semantic check based on the known zip context
            if "minesweeper" in content.lower():
                print("✅ Contextual check passed (Found target keyword).")
            else:
                print("⚠️ NOTICE: The word 'Minesweeper' wasn't in the README, check the prompt tuning.")

        else:
            pytest.fail(f"README.md was not generated by the agent at {readme_path}!")

    finally:
        # --- Cleanup ---
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)


def test_agent_readme_generation():
    """
    Tests the /readme macro by extracting a sample repo and running the agent on it.
    """
    zip_target = "test_data/minesweeper-solve.zip"

    repo_name = "minesweeper-solve"
    input_queue = [
        "/readme ./" + repo_name,
        "/send",
        "Looks good, task complete.",
        "/send",
        "/quit"
    ]

    run_automated_readme_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        max_calls_limit=40
    )


def test_agent_readme_generation_deep():
    """
    Tests the /readme macro by extracting a sample repo and running the agent on it.
    """
    zip_target = "test_data/minesweeper-solve.zip"

    repo_name = "minesweeper-solve"
    input_queue = [
        "/readme --deep ./" + repo_name,
        "/send",
        "Looks good, task complete.",
        "/send",
        "/quit"
    ]

    run_automated_readme_test(
        input_queue=input_queue,
        zip_file_path=zip_target,
        repo_name=repo_name,
        max_calls_limit=40
    )


def prepare_self_repo_sandbox(target_dir):
    """
    Copies the actual repository into a sandbox, ignoring cache and git history.
    """
    # Fixed: Corrected ".venv" patterns so virtualenv binary trees are strictly ignored
    ignore_patterns = shutil.ignore_patterns(
        ".git", ".venv", ".venv*", "venv", "env", "__pycache__",
        ".idea", ".vscode", "dist", "build", ".pytest_cache", "models",
        "README.md", "*.zip"
    )
    repo_dest = os.path.join(target_dir, "simple-coding-agent")

    shutil.copytree(PROJECT_ROOT, repo_dest, ignore=ignore_patterns)
    return repo_dest


def run_automated_self_readme_test(input_queue, repo_name, mode_flag, expected_keywords=None):
    """
    Custom runner that dynamically snapshots the live repository into a sandbox for self-testing.
    """
    print(f"🧪 Starting Live Self-Test for /readme {mode_flag}...", flush=True)

    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix="agent_self_readme_sandbox_")

    try:
        prepare_self_repo_sandbox(test_sandbox)

        simple_coding_agent.session_cwd = test_sandbox
        simple_coding_agent.FORCE_TESTING = True

        safety_counter = {"calls": 0, "max_calls": 40}

        def smart_input_mocker(prompt=""):
            safety_counter["calls"] += 1
            if safety_counter["calls"] > safety_counter["max_calls"]:
                return "/quit"

            prompt_str = str(prompt).lower()
            if "allow" in prompt_str or "y/n" in prompt_str:
                return "y"

            if input_queue:
                return input_queue.pop(0)

            return "/quit"

        os.chdir(test_sandbox)

        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                simple_coding_agent.main()
            except SystemExit:
                pass

        readme_path = os.path.join(test_sandbox, repo_name, "README.md")
        if not os.path.exists(readme_path):
            pytest.fail(f"README.md was not generated for the live agent codebase at {readme_path}!")

        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()

        word_count = len(content.split())
        print(f"📊 Self-README Word Count ({mode_flag}): {word_count} words", flush=True)

        if word_count < 150:
            pytest.fail(f"README is too shallow ({word_count} words).")

        if expected_keywords:
            missing = [kw for kw in expected_keywords if kw.lower() not in content.lower()]
            if missing:
                pytest.fail(f"README missing expected keywords: {missing}")

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)


def test_self_readme_generation_deep():
    """
    Tests /readme --deep against the current live state of the agent codebase.
    """
    input_queue = [
        "/readme --deep ./simple-coding-agent", "/send",
        "Looks good, task complete.", "/send", "/quit"
    ]
    run_automated_self_readme_test(
        input_queue=input_queue,
        repo_name="simple-coding-agent",
        mode_flag="--deep",
        expected_keywords=["agent"]
    )


def test_self_readme_generation_deep_ast():
    """
    Tests /readme --deep-ast against the current live state of the agent codebase.
    """
    input_queue = [
        "/readme --deep-ast ./simple-coding-agent", "/send",
        "Looks good, task complete.", "/send", "/quit"
    ]
    run_automated_self_readme_test(
        input_queue=input_queue,
        repo_name="simple-coding-agent",
        mode_flag="--deep-ast",
        expected_keywords=["agent"]
    )
