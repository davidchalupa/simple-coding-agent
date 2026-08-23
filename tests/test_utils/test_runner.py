import os
import tempfile
import shutil
import zipfile
import subprocess
import sys
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_coding_task_test(
        input_queue,
        zip_file_path=None,
        repo_name=None,
        setup_sandbox_hook=None,
        expected_file=None,
        target_file_path=None,
        check_for_change=False,
        expected_new_files=None,
        run_unittest_file=None,
        run_script_file=None,
        max_calls_limit=50,
        expected_keywords=None,
        custom_output_validator=None,
        custom_file_validator=None
):
    """
    Custom unified runner for agent tasks. Extracts a repo or runs a setup hook,
    runs the agent, validates modifications, unittests, and output files.
    """
    print(f"🧪 Starting Automated Agent Coding Task Test...", flush=True)

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
        simple_coding_agent.session_cwd = repo_sandbox
        simple_coding_agent.FORCE_TESTING = True

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

        # Move execution directly into the repo folder
        os.chdir(repo_sandbox)

        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                simple_coding_agent.main()
            except SystemExit:
                pass

        # --- Phase 1: Modification & File Generation Verification ---
        print("\n" + "=" * 60, flush=True)
        print("📊 Phase 1: Modification & File Generation Verification", flush=True)

        if check_for_change and target_file_path:
            sandbox_dest_path = os.path.join(repo_sandbox, target_file_path)
            file_changed = False
            if os.path.exists(sandbox_dest_path):
                with open(sandbox_dest_path, "r", encoding="utf-8") as f:
                    if f.read() != pristine_contents.get(sandbox_dest_path, ""):
                        file_changed = True

            if not file_changed:
                pytest.fail(f"❌ FAILED: Target file '{target_file_path}' was not modified.")
            else:
                print(f"✅ SUCCESS: Target file '{target_file_path}' was modified.")

        # Compile list of files to check for existence
        files_to_check = []
        if expected_file:
            files_to_check.append(expected_file)
        if expected_new_files:
            files_to_check.extend(expected_new_files)

        for f_name in files_to_check:
            target_file_path_abs = os.path.join(repo_sandbox, f_name)
            if not os.path.exists(target_file_path_abs):
                parent_fallback = os.path.join(test_sandbox, f_name)
                if os.path.exists(parent_fallback):
                    pytest.fail(f"❌ FAILED: {f_name} was generated in the wrong directory ({parent_fallback}).")
                else:
                    pytest.fail(f"❌ FAILED: {f_name} was not generated at all!")
            print(f"✅ SUCCESS: {f_name} was generated.")

        # --- Phase 2: Content Sanity Check ---
        if expected_file or run_script_file:
            print("\n" + "=" * 60, flush=True)
            print("🕵️  Phase 2: Content Sanity Check", flush=True)
            script_to_check = expected_file or run_script_file
            target_file_path_abs = os.path.join(repo_sandbox, script_to_check)

            if os.path.exists(target_file_path_abs):
                with open(target_file_path_abs, "r", encoding="utf-8") as f:
                    content = f.read()

                if expected_keywords:
                    missing_keywords = [kw for kw in expected_keywords if kw.lower() not in content.lower()]
                    if missing_keywords:
                        print(f"\n--- WRITTEN FILE CONTENT START ---\n{content}\n--- WRITTEN FILE CONTENT END ---\n")
                        pytest.fail(f"❌ FAILED: Script missing expected keywords: {missing_keywords}.")
                    else:
                        print("✅ Content sanity check passed. All expected keywords found.")
                else:
                    print("✅ Content sanity check skipped (no expected keywords provided).")

                if custom_file_validator:
                    custom_file_validator(target_file_path_abs)
            else:
                print(f"⚠️ Warning: File {target_file_path_abs} not found for sanity check.")

        # --- Phase 3: Execution Check ---
        print("\n" + "=" * 60, flush=True)
        print("🚀 Phase 3: Execution Check", flush=True)

        if run_unittest_file:
            try:
                test_file_abs = os.path.join(repo_sandbox, run_unittest_file)
                test_dir = os.path.dirname(test_file_abs)
                test_module = os.path.splitext(os.path.basename(test_file_abs))[0]

                current_env = os.environ.copy()
                python_paths = [repo_sandbox, test_dir]
                if current_env.get("PYTHONPATH"):
                    python_paths.append(current_env["PYTHONPATH"])
                current_env["PYTHONPATH"] = os.path.pathsep.join(python_paths)

                result = subprocess.run(
                    [sys.executable, "-m", "unittest", test_module],
                    capture_output=True, text=True, timeout=30, cwd=test_dir, env=current_env
                )

                print(result.stdout)
                print(result.stderr, file=sys.stderr)
                if result.returncode != 0:
                    pytest.fail(f"❌ FAILED: Unittests failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
                else:
                    print(f"✅ SUCCESS: Unittests passed.")
            except subprocess.TimeoutExpired:
                pytest.fail("❌ FAILED: Unittest execution timed out.")

        script_to_run = run_script_file
        if script_to_run:
            try:
                result = subprocess.run(
                    [sys.executable, script_to_run],
                    capture_output=True, text=True, timeout=60, cwd=repo_sandbox
                )

                print(result.stdout)
                print(result.stderr, file=sys.stderr)
                if result.returncode != 0:
                    pytest.fail(f"❌ FAILED: Script execution crashed:\n{result.stderr}")

                if custom_output_validator:
                    custom_output_validator(result.stdout)
            except subprocess.TimeoutExpired:
                pytest.fail("❌ FAILED: Script execution timed out.")

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)
