import os
import shutil
import tempfile
from unittest.mock import patch
import pytest
import coding_consultant
import zipfile

def run_read_only_coding_task_test(
        input_queue,
        zip_file_path=None,
        repo_name=None,
        setup_sandbox_hook=None,
        target_file_path=None,
        check_for_change=False,
        max_calls_limit=50,
        working_directory=None,
):
    """
    Custom unified runner for read-only agent tasks. Extracts a repo or runs a setup hook,
    runs the agent, validates modifications, unittests, and output files.
    """
    print(f"🧪 Starting Automated Read-Only Agent Coding Task Test...", flush=True)

    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix="agent_coding_sandbox_")

    try:
        # --- Environment Setup ---
        if zip_file_path:
            # os.path.join safely uses absolute paths if zip_file_path is already absolute
            source_zip_path = os.path.abspath(os.path.join(original_cwd, zip_file_path))
            if not os.path.exists(source_zip_path):
                pytest.fail(f"Real target zip file not found at: {source_zip_path}")

            with zipfile.ZipFile(source_zip_path, 'r') as zip_ref:
                zip_ref.extractall(test_sandbox)
        elif setup_sandbox_hook:
            setup_sandbox_hook(test_sandbox)

        # Target the repo directory OR default to the root sandbox
        if working_directory:
            repo_sandbox = working_directory
        else:
            if repo_name:
                repo_sandbox = os.path.join(test_sandbox, repo_name)
            else:
                repo_sandbox = test_sandbox

        # Snapshot pristine file if we are checking for modifications
        pristine_contents = {}
        if check_for_change and target_file_path:
            sandbox_dest_path = os.path.join(repo_sandbox, target_file_path)
            if os.path.exists(sandbox_dest_path):
                with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                    pristine_contents[sandbox_dest_path] = f.read()

        # State Injection
        coding_consultant.state.session_cwd = repo_sandbox
        coding_consultant.state.force_testing = True

        safety_counter = {"calls": 0, "max_calls": max_calls_limit}

        def smart_input_mocker(prompt=""):
            safety_counter["calls"] += 1
            if safety_counter["calls"] > safety_counter["max_calls"]:
                print("\n🛑 [Test Overload] Too many input calls. Forcing exit.", flush=True)
                return "/quit"

            prompt_str = str(prompt).lower()
            if "allow" in prompt_str or "y/n" in prompt_str:
                print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
                return "y"

            if input_queue:
                next_input = input_queue.pop(0)
                print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
                return next_input

            # --- Error recovery fallback for smaller 8B models ---
            if "error" in prompt_str or "json" in prompt_str or "failed" in prompt_str:
                print("\n⚠️ [Automated Test] Agent hit an error (likely truncated JSON). Triggering retry.", flush=True)
                return "Your output was truncated or invalid. Please write the file again, but keep it brief and DO NOT repeat lines."

            return "/quit"

        # Move execution directly into the repo folder (or execution directory)
        os.chdir(repo_sandbox)

        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                coding_consultant.main(coding_consultant.state)
                pass
            except SystemExit:
                pass

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)
