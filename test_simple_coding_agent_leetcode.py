import sys
import os
import tempfile
import shutil
import subprocess
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_test(input_queue, file_name, test_file_name, max_calls_limit=30):
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

        if file_name in sandbox_files and test_file_name in sandbox_files:
            print("✅ SUCCESS: The expected files were generated.", flush=True)
        else:
            print("❌ FAILED: The expected files were not found in the sandbox.", flush=True)
            pytest.fail("Files missing in the sandbox!")
            return  # Exit early if files are missing

        # --- Phase 2: Independent Verification ---
        print("\n" + "=" * 60, flush=True)
        print("🕵️  Phase 2: Independent Verification", flush=True)
        print("Running tests externally to verify agent logic...", flush=True)

        # Inject the sandbox directory into the PYTHONPATH for the subprocess
        current_env = os.environ.copy()
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

        # --- PHASE 2: Invariant-Driven Testing ---
        "Excellent. Now write a test file named `test_two_sum.py` using the `unittest` framework to test `two_sum.py`. "
        "CRITICAL INSTRUCTION: Do not hardcode exact expected index arrays (e.g., do not assert `res == [1, 2]`). "
        "Instead, write invariant-driven tests. For every test case, call `result = two_sum(nums, target)`, and assert the properties: "
        "1. `len(result) == 2` "
        "2. `result[0] != result[1]` "
        "3. `nums[result[0]] + nums[result[1]] == target`. "
        "Include at least 3 diverse edge cases (including negative numbers) using this exact property-checking pattern.",
        "/send",

        # --- PHASE 2.5: Enforcing fixes to reduce flakiness ---
        "Run the test suite using your `run_cmd` tool. If any tests fail, use your patching tools to fix the logic. "
        "If they all passed, just reply 'All good'.",
        "/send",

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    run_automated_test(input_queue, file_name="two_sum.py", test_file_name="test_two_sum.py")


def test_agent_workflow_lcs():
    input_queue = [
        # --- PHASE 1: Implementation ---
        "Can you write the code for longest common subsequence of two strings that returns the actual subsequence "
        "string and save the code to lcs.py? ",
        "/send",

        # --- PHASE 2: Invariant-Driven Testing with Trivial Constructions ---
        "Excellent. Now write unit tests for lcs.py and save them to test_lcs.py using the unittest framework. "
        "CRITICAL INSTRUCTION: Since multiple valid longest subsequences can exist, do not assert exact string matches. "
        "Instead, write a helper function `is_subsequence(sub, s)`. "
        "Furthermore, to avoid miscalculating expected lengths, strictly use these 3 trivial constructions for your test cases: "
        "1. Identical strings (e.g., s1='abc', s2='abc', expected_length=3) "
        "2. Empty string (e.g., s1='abc', s2='', expected_length=0) "
        "3. Strict subset (e.g., s1='abc', s2='xaxbxcx', expected_length=3). "
        "For each, assert: 1. `is_subsequence(result, s1)`, 2. `is_subsequence(result, s2)`, and 3. `len(result) == expected_length`.",
        # extra guardrail - sometimes in tries to write tests with array size 1, leading to flakiness
        "CRITICAL CONSTRAINT FOR TESTS: Do not write test cases that violate the problem's base preconditions. "
        "For example, do not test arrays with fewer than 2 elements for Two Sum, since a pair cannot be formed."
        "/send",

        # --- PHASE 2.5: Hardened Verification & Loop-Breaking ---
        "Run the test suite one final time using your `run_cmd` tool. "
        "CRITICAL RULE: If a test fails, DO NOT immediately rewrite `lcs.py`. Small AI models frequently write incorrect expected values in test files. "
        "First, manually verify if the `expected_length` in `test_lcs.py` is mathematically correct. If the test expectation is wrong, patch the test file! "
        "Only patch `lcs.py` if you are 100% sure the test is correct. "
        "Do NOT say 'All good' until the console explicitly shows that all tests passed.",
        "/send",

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    run_automated_test(input_queue, file_name="lcs.py", test_file_name="test_lcs.py")


def test_agent_workflow_knapsack_01():
    input_queue = [
        # --- PHASE 1: Implementation ---
        "Can you give me a code that solves the 0/1 knapsack problem with integer weights and capacity and save the code to knapsack_01.py? "
        " The function signature MUST be `knapsack_01(capacity, weights, values)`. "
        "Crucial: The function should ONLY return the maximum value (an integer). Do not return the list of selected items.",
        "/send",

        # --- PHASE 2: Invariant-Driven Testing ---
        "Excellent. Now write unittests for knapsack_01.py and save them to test_knapsack_01.py using the unittest framework. "
        "CRITICAL INSTRUCTION: Instead of only checking exact expected answers, use Metamorphic property-based testing. "
        "For a given list of weights and values, assert the following structural invariants: "
        "1. `self.assertEqual(knapsack(0, weights, values), 0)` (Zero capacity always yields 0) "
        "2. `self.assertEqual(knapsack(capacity, [], []), 0)` (No items always yields 0) "
        "3. `self.assertLessEqual(knapsack(capacity, weights, values), sum(values))` (Max value cannot exceed the sum of all item values) "
        "4. `self.assertGreaterEqual(knapsack(capacity + 1, weights, values), knapsack(capacity, weights, values))` (Increasing capacity never decreases the result). "
        "Include these property checks alongside at least 2 standard deterministic cases.",
        "/send",

        # --- PHASE 2.5: Hardened Active Verification ---
        "Run the test suite one final time using your `run_cmd` tool to verify your changes. "
        "If you see ANY failures or AssertionErrors in the terminal output, you must immediately patch the source code or the test file to correct the expected values. "
        "Do NOT say 'All good' until you have executed the tool and the console explicitly shows that all tests passed.",
        "/send",

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    run_automated_test(input_queue, file_name="knapsack_01.py", test_file_name="test_knapsack_01.py")


def test_agent_workflow_trap():
    input_queue = [
        # --- PHASE 1: Implementation ---
        "Write a Python script to solve the problem below, with comments in the code explaining the rationale of the algorithm.",
        "Given n non-negative integers representing an elevation map where the width of each bar is 1, compute how much water it can trap after raining.",
        "Example 1:",
        "Elevation map:",
        ".......x....",
        "...x---xx-x.",
        ".x-xx-xxxxxx",
        "Input: height = [0,1,0,2,1,0,1,3,2,1,2,1]",
        "Output: 6",
        "Explanation: In the elevation map above . means empty cell, x means rock, - means water. This map is represented by array [0,1,0,2,1,0,1,3,2,1,2,1]. In this case, 6 units of rain water are being trapped.",
        "Example 2:",
        "Input: height = [4,2,0,3,2,5]",
        "Output: 9",
        "/send",

        # --- PHASE 1.5: Saving ---
        "Great. Now save the script written above to file named trap.py.",
        "/send",

        # --- PHASE 2: Testing ---
        "Excellent. Now write unittests for trap.py and save them to test_trap.py using the unittest framework. "
        "Include only tests for the two examples given above. These are sufficient.",
        "/send",

        # --- PHASE 2.5: Hardened Active Verification ---
        "Run the test suite one final time using your `run_cmd` tool to verify your changes. "
        "If you see ANY failures or AssertionErrors in the terminal output, you must immediately patch the source code or the test file to correct the expected values. "
        "Do NOT say 'All good' until you have executed the tool and the console explicitly shows that all tests passed.",
        "/send",

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    run_automated_test(input_queue, file_name="trap.py", test_file_name="test_trap.py")

