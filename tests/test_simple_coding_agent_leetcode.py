from tests.test_utils.test_runner import run_automated_coding_task_test


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
        "Include at least 3 diverse edge cases (including negative numbers) using this exact property-checking pattern. "
        "CRITICAL: For each test case, choose your nums/target so that a valid pair is GUARANTEED to exist - "
        "e.g. pick two arbitrary numbers first (like -3 and -4), compute their sum yourself, and set target to "
        "exactly that sum. Do NOT invent nums and target independently and assume they happen to sum correctly."
        "If they all passed, just reply 'All good'.",
        "/send",

        # --- PHASE 2.5: Enforcing fixes to reduce flakiness ---
        "Run the test suite using your `run_cmd` tool. If any tests fail, use your patching tools to fix the logic. "
        "/send",

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        expected_file="two_sum.py",
        expected_new_files=["test_two_sum.py"],
        run_unittest_file="test_two_sum.py"
    )


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

    run_automated_coding_task_test(
        input_queue=input_queue,
        expected_file="lcs.py",
        expected_new_files=["test_lcs.py"],
        run_unittest_file="test_lcs.py"
    )


def test_agent_workflow_knapsack_01():
    input_queue = [
        # --- PHASE 1: Implementation ---
        "Can you give me a code that solves the 0/1 knapsack problem with integer weights and capacity and save the code to knapsack_01.py? "
        " The function signature MUST be `knapsack_01(capacity, weights, values)`. "
        "Crucial: The function should ONLY return the maximum value (an integer). Do not return the list of selected items.",
        "/send",

        # --- PHASE 2: Invariant-Driven Testing ---
        "Excellent. Now write unittests for knapsack_01.py and save them to test_knapsack_01.py using the unittest "
        "framework. CRITICAL INSTRUCTION: Instead of only checking exact expected answers, use Metamorphic "
        "property-based testing. For a given list of weights and values, assert the following structural invariants: "
        "1. `self.assertEqual(knapsack(0, weights, values), 0)` (Zero capacity always yields 0) "
        "2. `self.assertEqual(knapsack(capacity, [], []), 0)` (No items always yields 0) "
        "3. `self.assertLessEqual(knapsack(capacity, weights, values), sum(values))` (Max value cannot exceed the sum "
        "of all item values) "
        "4. `self.assertGreaterEqual(knapsack(capacity + 1, weights, values), knapsack(capacity, weights, values))` "
        "(Increasing capacity never decreases the result). Strictly avoid hardcoded deterministic test cases or magic "
        "numbers. Use the `write_file` tool with the full test file content in the JSON `content` field.",
        "/send",

        # --- PHASE 2.5: Hardened Active Verification ---
        "Run the test suite one final time using your `run_cmd` tool to verify your changes. If you see ANY failures "
        "or AssertionErrors in the terminal output, you must immediately patch the source code or the test file to "
        "correct the expected values. Do NOT say 'All good' until you have executed the tool and the console "
        "explicitly shows that all tests passed.",
        "/send",

        # --- PHASE 3: Graceful Exit ---
        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        expected_file="knapsack_01.py",
        expected_new_files=["test_knapsack_01.py"],
        run_unittest_file="test_knapsack_01.py"
    )


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

    run_automated_coding_task_test(
        input_queue=input_queue,
        expected_file="trap.py",
        expected_new_files=["test_trap.py"],
        run_unittest_file="test_trap.py"
    )
