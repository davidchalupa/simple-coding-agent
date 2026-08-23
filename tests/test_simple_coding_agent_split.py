import os
import shutil
from tests.test_utils.test_runner import run_automated_coding_task_test


def setup_split_fixture(target_file, validation_test=None):
    """
    Setup hook that copies target source files and optional test files
    from the local repository into the temporary sandbox.
    """
    def hook(sandbox_path):
        # Copy target file into sandbox
        dest_target = os.path.join(sandbox_path, target_file)
        os.makedirs(os.path.dirname(dest_target), exist_ok=True)
        shutil.copy2(target_file, dest_target)

        # Copy validation unit test file into sandbox (if applicable)
        if validation_test:
            dest_test = os.path.join(sandbox_path, validation_test)
            os.makedirs(os.path.dirname(dest_test), exist_ok=True)
            shutil.copy2(validation_test, dest_test)

    return hook


def test_agent_split_advisor_mode():
    target_file = "test_data/test_split/graph_drawing_explorer.py"

    input_queue = [
        f"/split {target_file}",
        "/send",

        "Looks good. Task Complete.",
        "/send",

        # --- PHASE 2: Graceful Exit ---
        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        setup_sandbox_hook=setup_split_fixture(target_file)
    )


def test_agent_split_execute_mode():
    target_file = "test_data/test_split_execute/ecommerce_order_processor.py"
    validation_test = "test_data/test_split_execute/test_ecommerce_order_processor.py"

    input_queue = [
        f"/split --execute {target_file}",
        "/send",

        "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        setup_sandbox_hook=setup_split_fixture(target_file, validation_test),
        run_unittest_file=validation_test,
        max_calls_limit=45
    )
