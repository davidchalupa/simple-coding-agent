import sys
import os
import tempfile
import shutil
import subprocess
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_test(input_queue, max_calls_limit=10):
    print("🧪 Starting Automated Agent Flow Test...", flush=True)

    original_cwd = os.getcwd()  # STORE THE ORIGINAL DIRECTORY
    test_sandbox = tempfile.mkdtemp(prefix="agent_test_sandbox_")
    print(f"📁 Created temporary sandbox at: {test_sandbox}", flush=True)

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

        # Catch tool approvals / confirmations first
        if "allow" in prompt_str or "promote" in prompt_str or "y/n" in prompt_str:
            print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
            return "y"

        # Otherwise, consume from the queue
        if input_queue:
            next_input = input_queue.pop(0)
            print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
            return next_input

        # Default fallback
        return "/quit"

    try:
        # FIX: Change the actual OS current working directory to the sandbox.
        # This ensures any 'run_cmd' subprocesses spawned by the agent run here.
        os.chdir(test_sandbox)

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

        # --- Phase 2: Independent Verification ---
        print("\n" + "=" * 60, flush=True)
        print("🕵️  Phase 2: Independent Verification", flush=True)
        print("Running tests externally to verify agent logic...", flush=True)

        # Inject the sandbox directory into the PYTHONPATH for the subprocess
        current_env = os.environ.copy()
        current_env["PYTHONPATH"] = os.path.pathsep.join([test_sandbox, current_env.get("PYTHONPATH", "")])

        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", test_sandbox, "-p", "test_*.py"],
            capture_output=True,
            text=True,
            env=current_env
        )

        if result.returncode == 0:
            print("✅ VERIFICATION PASSED! The LLM wrote valid, functioning code.")
            print("--- Test Output ---")
            print(result.stderr.strip())
        else:
            print("❌ VERIFICATION FAILED! The generated tests failed.")
            print("--- Error Output (stdout) ---")
            print(result.stdout)
            print("--- Error Output (stderr) ---")
            print(result.stderr)
            pytest.fail("Verification failed!")

    finally:
        # FIX: Always restore the original working directory before cleaning up
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)


def test_agent_workflow_two_sum():
    input_queue = [
        # --- PHASE 1: Implementation ---
        "Write a Python script named `two_sum.py` with a function `two_sum(nums, target)` that returns the indices "
        "of the two numbers in nums array such that they add up to target. "
        "You may assume that each input would have exactly one solution, and you may not use the same element twice. ",
        "/send",

        # --- PHASE 2: Testing ---
        "Excellent. Now write a test file named `test_two_sum.py` using the `unittest` framework to test the `two_sum.py` script you just created. Include at least 3 different edge cases.",
        "/send",

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    run_automated_test(input_queue)


# def test_agent_workflow_lcs():
#     input_queue = [
#         # --- PHASE 1: Implementation ---
#         "Can you write the code for longest common subsequence of two strings that returns the actual subsequence ",
#         "string and save the code to lcs.py? ",
#         "/send",
#
#         # --- PHASE 2: Testing ---
#         "Excellent. Can you now write unit tests for lcs.py and save them to test_lcs.py?",
#         "/send",
#
#         # --- PHASE 3: Graceful Exit ---
#         "/quit"
#     ]
#
#     run_automated_test(input_queue)
#
#
# def test_agent_workflow_knapsack_01():
#     input_queue = [
#         # --- PHASE 1: Implementation ---
#         "Can you give me a code that solves the 0/1 knapsack problem with integer weights and capacity and save",
#           "the code to knapsack_01.py?",
#
#
#         "/send",
#
#         # --- PHASE 2: Testing ---
#         "Excellent. Can you now write unittests for knapsack_01.py? Make sure you use relative imports so we can ",
#         "run the test. Make sure that expected values checked are correct. Save the tests to test_knapsack_01.py. ",
#         "/send",
#
#         # --- PHASE 3: Graceful Exit ---
#         "/quit"
#     ]
#
#     run_automated_test(input_queue)
