import sys
import os
import tempfile
import shutil
import zipfile
import subprocess
from unittest.mock import patch
import pytest

import simple_coding_agent


def run_automated_agent_test(
        input_queue,
        target_file_path,
        max_calls_limit=30,
        expected_new_files=None,
):
    print(f"🧪 Starting Automated Agent Flow Test for: {target_file_path}...", flush=True)

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

    # --- State Setup & Injection ---
    orig_session_cwd = getattr(simple_coding_agent, "session_cwd", None)
    orig_force_testing = getattr(simple_coding_agent, "FORCE_TESTING", False)

    simple_coding_agent.messages = []
    simple_coding_agent.is_split_mode = False
    simple_coding_agent.is_execute_mode = False
    simple_coding_agent.sandbox_directory = None
    simple_coding_agent.automated_followup = None
    simple_coding_agent.has_prompted_for_tests = False

    simple_coding_agent.session_cwd = test_sandbox
    simple_coding_agent.FORCE_TESTING = False

    safety_counter = {"calls": 0, "max_calls": max_calls_limit}

    def smart_input_mocker(prompt=""):
        safety_counter["calls"] += 1
        if safety_counter["calls"] > safety_counter["max_calls"]:
            print("\n🛑 [Test Overload] Exceeded maximum input calls limit. Forcing exit.", flush=True)
            return "/quit"

        prompt_str = str(prompt).lower()

        if "allow" in prompt_str or "y/n" in prompt_str or "edit" in prompt_str:
            print("\n🤖 [Automated Test] Auto-approving tool execution: 'y'", flush=True)
            return "y"

        if input_queue:
            next_input = input_queue.pop(0)
            print(f"\n⌨️  [Automated Test] Typing: {next_input}", flush=True)
            return next_input

        return "/quit"

    try:
        os.chdir(test_sandbox)

        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                simple_coding_agent.main()
            except SystemExit as e:
                print(f"\n🏁 Agent session terminated with code: {e.code}", flush=True)

        # --- Phase 1b: Expected New File(s) Existence Check ---
        if expected_new_files:
            print("\n" + "=" * 60, flush=True)
            print("📄 Phase 1b: New File Existence Verification", flush=True)

            for rel_path in expected_new_files:
                abs_path = os.path.join(test_sandbox, rel_path)
                if not os.path.exists(abs_path):
                    print(f"❌ Expected file not found: '{rel_path}'", flush=True)
                    pytest.fail(f"Agent did not create expected file: '{rel_path}'")
                else:
                    print(f"✅ Expected file exists: '{rel_path}'", flush=True)

        # --- Phase 2: Syntax Verification ---
        print("\n" + "=" * 60, flush=True)
        print("🕵️  Phase 2: Linter & Syntax Verification", flush=True)

        current_env = os.environ.copy()
        current_env["PYTHONPATH"] = os.path.pathsep.join([test_sandbox, current_env.get("PYTHONPATH", "")])

        check_passed = True
        for root, _, files in os.walk(test_sandbox):
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
                        print(f"❌ SYNTAX ERROR in {file}:\n{result.stderr}", flush=True)
                        check_passed = False
                    else:
                        print(f"✅ Syntax valid: {file}", flush=True)

        if not check_passed:
            pytest.fail("Syntax verification failed on modified code!")

    finally:
        os.chdir(original_cwd)
        simple_coding_agent.session_cwd = orig_session_cwd
        simple_coding_agent.FORCE_TESTING = orig_force_testing
        print(f"\n🧹 Cleaned up temporary sandbox.", flush=True)


def test_agent_modify_invcalc():
    target_file = "test_data/invcalc.py"
    output_file = "test_data/invcalc_extended.py"

    input_queue = [
        # --- Turn 1: Context & Source Inspection ---
        f"Read the full contents of {target_file} to understand its current structure and QTableWidget implementation.",
        "/send",

        # --- Turn 2: Strategy & Architecture Blueprint ---
        "We need to add multi-cell copy support to the table on Ctrl+C so users can paste data into Excel or LibreOffice Calc. "
        "Here is the exact architectural blueprint to follow:\n"
        "1. Create a custom subclass `CopyableTableWidget` that inherits from `QTableWidget`.\n"
        "2. Override `keyPressEvent(self, event)` to intercept Ctrl+C (`QKeySequence.Copy` or `Qt.Key_C` with `Qt.ControlModifier`).\n"
        "3. Inside `keyPressEvent`, use `self.selectedIndexes()` to compute the minimum and maximum row/column bounding box.\n"
        "4. Construct a 2D Tab-Separated Values (TSV) string where columns are separated by '\\t' and rows by '\\n'.\n"
        "5. Copy this TSV string to the system clipboard using `QApplication.clipboard().setText(...)`.\n"
        "6. Replace the standard `QTableWidget` instance in the main window with `CopyableTableWidget`.\n"
        "Do you understand this strategy?",
        "/send",

        # --- Turn 3: Implementation Execution ---
        f"Great. Now apply this exact pattern to create a modified version of the app. "
        f"Write the complete, syntax-valid updated script to `{output_file}`. "
        "Ensure all required PyQt5 imports (such as `Qt` and `QApplication`) are properly included and formatting is preserved.",
        "/send",

        "/quit"
    ]

    run_automated_agent_test(
        input_queue=input_queue,
        target_file_path=target_file,
        max_calls_limit=30,
        expected_new_files=[output_file],
    )
