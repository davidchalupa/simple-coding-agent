import os
import shutil
import py_compile

from tests.test_utils.test_runner import run_automated_coding_task_test


def setup_invcalc_sandbox(sandbox_dir):
    """Copies the test_data/invcalc.py file into the sandbox to protect the original."""
    original_cwd = os.getcwd()
    source_path = os.path.join(original_cwd, "test_data", "invcalc.py")
    sandbox_dest_path = os.path.join(sandbox_dir, "test_data", "invcalc.py")

    os.makedirs(os.path.dirname(sandbox_dest_path), exist_ok=True)
    shutil.copy2(source_path, sandbox_dest_path)


def validate_python_syntax(file_path):
    """Replicates the Phase 2 Linter & Syntax Verification from the old runner."""
    print(f"🕵️ Verifying syntax for: {file_path}", flush=True)
    try:
        py_compile.compile(file_path, doraise=True)
        print(f"✅ Syntax valid: {file_path}", flush=True)
    except py_compile.PyCompileError as e:
        raise AssertionError(f"❌ SYNTAX ERROR in {file_path}:\n{e}")


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

    run_automated_coding_task_test(
        input_queue=input_queue,
        setup_sandbox_hook=setup_invcalc_sandbox,
        expected_file=output_file,
        max_calls_limit=30,
        custom_file_validator=validate_python_syntax
    )
