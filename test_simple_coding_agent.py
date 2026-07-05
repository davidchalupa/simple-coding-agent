import sys
import os
import tempfile
import shutil
import subprocess
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_test():
    print("🧪 Starting Automated Agent Flow Test...", flush=True)

    test_sandbox = tempfile.mkdtemp(prefix="agent_test_sandbox_")
    print(f"📁 Created temporary sandbox at: {test_sandbox}", flush=True)

    # --- Direct State Injection ---
    # Override the agent's internal state directly since the module has already loaded
    simple_coding_agent.session_cwd = test_sandbox
    # for now, we will not force the agent to self-check, it can sometimes be surprised that the script
    # has no output
    # simple_coding_agent.FORCE_TESTING = True

    input_queue = [
        # --- PHASE 1: Implementation ---
        "Write a Python script named `two_sum.py` with a function `two_sum(nums, target)` that returns the indices "
        "of the two numbers in nums array such that they add up to target. "
        "You may assume that each input would have exactly one solution, and you may not use the same element twice. ",
        "/send",

        # (Because FORCE_TESTING is True, the agent will automatically self-trigger a check here)

        # --- PHASE 2: Testing ---
        "Excellent. Now write a test file named `test_two_sum.py` using the `unittest` framework to test the `two_sum.py` script you just created. Include at least 3 different edge cases.",
        "/send",

        # (The agent will automatically self-trigger another check here)

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    safety_counter = {"calls": 0, "max_calls": 50}

    def smart_input_mocker(prompt=""):
        safety_counter["calls"] += 1
        if safety_counter["calls"] > safety_counter["max_calls"]:
            print("\n🛑 [Test Overload] Too many input calls. Forcing exit.", flush=True)
            return "/quit"

        if "Allow this action" in prompt or "promote" in prompt:
            print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
            return "y"

        if not prompt:
            if input_queue:
                next_input = input_queue.pop(0)
                print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
                return next_input
            else:
                return "/quit"

        return "y"

    try:
        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                simple_coding_agent.main()
            except SystemExit as e:
                print(f"\n🏁 Agent terminated with code: {e.code}", flush=True)

        sandbox_files = os.listdir(test_sandbox)
        print("\n" + "=" * 60, flush=True)
        print("📊 Phase 1: Generation Results", flush=True)
        print(f"Files generated in sandbox: {sandbox_files}", flush=True)

        if "two_sum.py" in sandbox_files and "test_two_sum.py" in sandbox_files:
            print("✅ SUCCESS: The expected files were generated.", flush=True)
        else:
            print("❌ FAILED: The expected files were not found in the sandbox.", flush=True)
            return  # Exit early if files are missing

        # --- NEW PHASE: Independent Verification ---
        print("\n" + "=" * 60, flush=True)
        print("🕵️  Phase 2: Independent Verification", flush=True)
        print("Running tests externally to verify agent logic...", flush=True)

        # Use sys.executable to ensure we run tests using the exact same Python environment
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", test_sandbox, "-p", "test_*.py"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            print("✅ VERIFICATION PASSED! The LLM wrote valid, functioning code.")
            print("--- Test Output ---")
            print(result.stderr.strip())  # Unittest prints results to stderr by default
        else:
            print("❌ VERIFICATION FAILED! The generated tests failed.")
            print("--- Error Output ---")
            print(result.stdout)
            print(result.stderr)

    finally:
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)


def test_agent_two_sum_workflow():
    run_automated_test()
