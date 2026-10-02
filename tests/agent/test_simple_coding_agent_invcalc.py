import os
import shutil
import py_compile
import pytest

from tests.agent.test_utils.test_runner import run_automated_coding_task_test


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


def validate_invcalc_extended(extended_file_path):
    """
    Validates that invcalc_extended.py is syntactically valid and
    differs from the original invcalc.py.
    """
    # Assuming validate_python_syntax is available in the test environment
    validate_python_syntax(extended_file_path)

    base_dir = os.path.dirname(extended_file_path)
    original_file_path = os.path.join(base_dir, "invcalc.py")

    if not os.path.exists(original_file_path):
        pytest.fail(f"❌ FAILED: Reference file '{original_file_path}' does not exist.")

    with open(original_file_path, "r", encoding="utf-8") as f:
        original_code = f.read()

    with open(extended_file_path, "r", encoding="utf-8") as f:
        extended_code = f.read()

    if original_code.strip() == extended_code.strip():
        pytest.fail(
            f"❌ FAILED: '{os.path.basename(extended_file_path)}' is identical "
            f"to '{os.path.basename(original_file_path)}'. No changes were applied!"
        )

    print("✅ SUCCESS: 'invcalc_extended.py' was successfully updated and differs from 'invcalc.py'.", flush=True)


def test_agent_modify_invcalc_append_only():
    target_file = "test_data/invcalc.py"
    output_file = "test_data/invcalc_extended.py"

    input_queue = [
        # --- Turn 1: Copy File ---
        f"Copy '{target_file}' to '{output_file}' using the `run_cmd` tool (use `cp` command). Make sure you respect the correct "
        f"signature of the `run_cmd` tool! No output is okay, `cp` does not provide output normally.",
        "/send",

        # --- Turn 2: Context & Source Inspection ---
        f"Now read the full contents of {output_file} to understand its current structure and QTableWidget implementation.",
        "/send",

        # --- Turn 3: Write Subclass & Append to Copy ---
        "We need to add multi-cell copy support to the table on Ctrl+C so users can paste data into Excel or LibreOffice Calc. "
        "Here is the architectural blueprint for the new widget:\n"
        "1. Create a custom subclass `CopyableTableWidget` that inherits from `QTableWidget`.\n"
        "2. Override `keyPressEvent(self, event)` to intercept Ctrl+C (`QKeySequence.Copy` or `Qt.Key_C` with `Qt.ControlModifier`).\n"
        "3. Inside `keyPressEvent`, use `self.selectedIndexes()` to compute the minimum and maximum row/column bounding box.\n"
        "4. Construct a 2D Tab-Separated Values (TSV) string where columns are separated by '\\t' and rows by '\\n'.\n"
        "5. Copy this TSV string to the system clipboard using `QApplication.clipboard().setText(...)`.\n\n"
        f"Use the `append_file` tool to append this complete `CopyableTableWidget` class to the bottom of `{output_file}`.\n"
        f"CRITICAL: You MUST output the `append_file` tool call with the fully escaped code in the JSON `content` field.\n",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        setup_sandbox_hook=setup_invcalc_sandbox,  # Assuming imported
        expected_file=output_file,
        max_calls_limit=30,
        expected_keywords=["CopyableTableWidget", "selectedIndexes", "clipboard"],
        custom_file_validator=validate_invcalc_extended
    )



def test_agent_modify_invcalc_append_and_update():
    target_file = "test_data/invcalc.py"
    output_file = "test_data/invcalc_extended.py"

    input_queue = [
        # --- Turn 1: Copy File ---
        f"Copy '{target_file}' to '{output_file}' using the `run_cmd` tool (use `cp` command). Make sure you respect the correct "
        f"signature of the `run_cmd` tool! No output is okay, `cp` does not provide output normally.",
        "/send",

        # --- Turn 2: Context & Source Inspection ---
        f"Now read the full contents of {output_file} to understand its current structure and QTableWidget implementation.",
        "/send",

        # --- Turn 3: Write Subclass & Append to Copy ---
        "We need to add multi-cell copy support to the table on Ctrl+C so users can paste data into Excel or LibreOffice Calc. "
        "Here is the architectural blueprint for the new widget:\n"
        "1. Create a custom subclass `CopyableTableWidget` that inherits from `QTableWidget`.\n"
        "2. Override `keyPressEvent(self, event)` to intercept Ctrl+C (`QKeySequence.Copy` or `Qt.Key_C` with `Qt.ControlModifier`).\n"
        "3. Inside `keyPressEvent`, use `self.selectedIndexes()` to compute the minimum and maximum row/column bounding box.\n"
        "4. Construct a 2D Tab-Separated Values (TSV) string where columns are separated by '\\t' and rows by '\\n'.\n"
        "5. Copy this TSV string to the system clipboard using `QApplication.clipboard().setText(...)`.\n\n"
        f"Use the `append_file` tool to append this complete `CopyableTableWidget` class to the bottom of `{output_file}`.\n"
        f"CRITICAL: You MUST output the `append_file` tool call with the fully escaped code in the JSON `content` field.\n",
        "/send",

        # --- Turn 4: Apply Widget & Update Imports ---
        f"Great. Now we must apply this new widget in `{output_file}`.\n"
        "Replace the standard `QTableWidget` instance in the main window with the new `CopyableTableWidget`.\n"
        "Use `patch_file` for this not `replace_lines`, it's a small change!\n",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        setup_sandbox_hook=setup_invcalc_sandbox,  # Assuming imported
        expected_file=output_file,
        max_calls_limit=30,
        expected_keywords=["CopyableTableWidget", "selectedIndexes", "clipboard"],
        custom_file_validator=validate_invcalc_extended
    )


@pytest.mark.skip(reason="Skipped due to instability")
def test_agent_modify_invcalc_full_rewrite():
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
        "Do not make any modifications to the existing file. Only write down necessary plans and code, if needed.\n"
        "Do you understand this strategy?",
        "/send",

        # --- Turn 3: Implementation Execution ---
        f"Great. Now apply this exact pattern to create a modified version of the app. "
        f"Use the `write_file` tool to save the complete, syntax-valid updated script to "
        f"`{output_file}`. Ensure all required PyQt5 imports (such as `Qt` and `QApplication`) "
        f"are properly included and formatting is preserved. "
        f"You MUST output a `write_file` tool call with the full file content in the JSON "
        f"`content` field — do not just show the code in a markdown block.",
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
