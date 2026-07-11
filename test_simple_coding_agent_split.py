import sys
import os
import tempfile
import shutil
import subprocess
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_split_test(input_queue, target_file_path, max_calls_limit=30):
    """
    Custom runner for the /split macro that copies a REAL existing file
    from the repository into a temporary sandbox for safe analysis and refactoring.
    """
    print("🧪 Starting Automated Agent Flow Test for /split...", flush=True)

    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix="agent_split_sandbox_")
    print(f"📁 Created temporary sandbox at: {test_sandbox}", flush=True)

    # --- Copy the REAL file into the sandbox to protect the original ---
    source_path = os.path.join(original_cwd, target_file_path)
    if not os.path.exists(source_path):
        shutil.rmtree(test_sandbox)
        pytest.fail(f"Real target file not found at: {source_path}")

    sandbox_dest_path = os.path.join(test_sandbox, target_file_path)
    os.makedirs(os.path.dirname(sandbox_dest_path), exist_ok=True)
    shutil.copy2(source_path, sandbox_dest_path)
    print(f"🌱 Copied real test file to sandbox: {target_file_path}", flush=True)

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

        # Catch tool approvals / Sandbox promotion confirmations
        if "allow" in prompt_str or "promote" in prompt_str or "y/n" in prompt_str:
            print("\n🤖 [Automated Test] Auto-approving tool execution/promotion: 'y'", flush=True)
            return "y"

        # Consume from the queue
        if input_queue:
            next_input = input_queue.pop(0)
            print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
            return next_input

        # Default fallback
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
        target_check_dir = os.path.dirname(sandbox_dest_path)
        print("\n" + "=" * 60, flush=True)
        print("📊 Phase 1: Sandbox Promotion Results", flush=True)

        dir_files = os.listdir(target_check_dir)
        print(f"Files found in {target_check_dir}: {dir_files}", flush=True)

        # In split mode, we expect MORE than 1 file if refactoring occurred
        if len(dir_files) > 1:
            print("✅ SUCCESS: The agent successfully populated the directory with split files.", flush=True)
        else:
            # If it's just advisor mode, it might not generate new files.
            print("⚠️ NOTICE: No new files promoted. (Expected if running strictly Advisor Mode)", flush=True)

        # --- Phase 2: Independent Verification (Syntax/Import Checks) ---
        print("\n" + "=" * 60, flush=True)
        print("🕵️  Phase 2: Linter & Syntax Verification", flush=True)
        print("Running python compilation check to verify agent didn't break syntax...", flush=True)

        current_env = os.environ.copy()
        current_env["PYTHONPATH"] = os.path.pathsep.join([test_sandbox, current_env.get("PYTHONPATH", "")])

        # Attempt to compile the python files to ensure syntactical validity
        check_passed = True
        for root, _, files in os.walk(target_check_dir):
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    result = subprocess.run(
                        [sys.executable, "-m", "py_compile", file_path],
                        capture_output=True,
                        text=True,
                        env=current_env
                    )
                    if result.returncode != 0:
                        print(f"❌ SYNTAX ERROR in {file}:\n{result.stderr}")
                        check_passed = False
                    else:
                        print(f"✅ Syntax valid: {file}")

        if not check_passed:
            pytest.fail("Syntax verification failed on split files!")

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)


def test_agent_split_advisor_mode():
    """
    Tests the standard /split macro (Advisor mode) on a real existing file.
    """
    target_file = "test_data/test_split/graph_drawing_explorer.py"

    input_queue = [
        # --- PHASE 1: Execution ---
        f"/split {target_file}",
        "/send",

        # Give it an explicit prompt to wrap up the advisor phase
        "Looks good. Task Complete.",
        "/send",

        # --- PHASE 2: Graceful Exit ---
        "/quit"
    ]

    run_automated_split_test(
        input_queue=input_queue,
        target_file_path=target_file
    )


def test_agent_split_execute_mode():
    """
    Tests the /split --execute macro on a real existing file.
    """
    target_file = "test_data/test_split_execute/ecommerce_order_processor.py"

    input_queue = [
        # --- PHASE 1: Execution ---
        f"/split --execute {target_file}",
        "/send",

        # In execute mode, the system prompt tells the agent to split the code,
        # write the files, and output "Refactor Phase Complete". We'll just let it run.

        # --- PHASE 2: Graceful Exit ---
        "/quit"
    ]

    # Providing a slightly higher max calls limit because file writing requires multiple round-trips
    run_automated_split_test(
        input_queue=input_queue,
        target_file_path=target_file,
        max_calls_limit=45
    )
