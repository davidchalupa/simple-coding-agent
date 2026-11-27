import os
import shutil
import tempfile
import zipfile
import pytest

from tests.agent.test_utils.grounding_verifier import GroundingVerifier
from tests.agent.test_utils.test_runner import run_automated_coding_task_test


# Project root directory (parent of the tests/ folder)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
TESTS_DIR = os.path.dirname(__file__)


def resolve_zip_path(zip_file_path):
    """Finds the zip file across project root and tests directory candidate locations."""
    candidates = [
        os.path.abspath(os.path.join(PROJECT_ROOT, zip_file_path)),
        os.path.abspath(os.path.join(TESTS_DIR, zip_file_path)),
        os.path.abspath(os.path.join(PROJECT_ROOT, "tests", zip_file_path)),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def prepare_self_repo_sandbox(target_dir):
    """
    Copies the current repository into a sandbox, ignoring cache and git history.
    Passed directly to run_automated_coding_task_test as setup_sandbox_hook.
    """
    ignore_patterns = shutil.ignore_patterns(
        ".git", ".venv", ".venv*", "venv", "env", "__pycache__",
        ".idea", ".vscode", "dist", "build", ".pytest_cache", "models", "README.md",
    )
    repo_dest = os.path.join(target_dir, "simple-coding-agent")
    shutil.copytree(PROJECT_ROOT, repo_dest, ignore=ignore_patterns)


def _format_readme_command(mode_flag=None):
    """Builds '/readme .' for default mode or '/readme <flag> .' when a flag is given."""
    flag = (mode_flag or "").strip()
    return f"/readme {flag} ." if flag else "/readme ."


def _build_input_queue(mode_flag=None):
    return [
        "Keep the generated README concise. Do not repeat sections or dummy bash commands.", "/send",
        # Fix: The runner already `cd`s into repo_name, so we target "." instead of "./repo_name"
        _format_readme_command(mode_flag), "/send",
        "Looks good, task complete.", "/send", "/quit",
    ]


def _print_mode_score(label, score, include_ref_count=False):
    print(f"\nMode [{label}]:")
    print(f"  • Groundedness Precision: {score['groundedness_score']}%")
    print(f"  • Info Density:           {score['density_score']}")
    if include_ref_count:
        print(f"  • Total Code References:  {score['total_code_references']}")
    print(f"  • Suspected Hallucinations:\n    {score['hallucination_candidates']}")


def run_readme_and_capture(zip_file_path, repo_name, mode_flag=None):
    """Executes the agent via unified runner and captures generated README content."""
    captured = {}

    def capture_readme_content(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            captured["content"] = f.read()

    run_automated_coding_task_test(
        input_queue=_build_input_queue(mode_flag),
        zip_file_path=resolve_zip_path(zip_file_path),
        repo_name=repo_name,
        expected_file="README.md",
        max_calls_limit=40,
        custom_file_validator=capture_readme_content,
    )

    return captured.get("content", "")


def run_self_readme_and_capture(repo_name, mode_flag=None):
    """Executes the agent against the live repository via unified runner and captures README content."""
    captured = {}

    def capture_readme_content(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            captured["content"] = f.read()

    run_automated_coding_task_test(
        input_queue=_build_input_queue(mode_flag),
        setup_sandbox_hook=prepare_self_repo_sandbox,
        repo_name=repo_name,
        expected_file="README.md",
        max_calls_limit=80,
        custom_file_validator=capture_readme_content,
    )

    return captured.get("content", "")


@pytest.mark.skip(reason="Run only on-demand if benchmarking needed")
def test_minesweeper_macro_benchmark():
    zip_target = "test_data/minesweeper-solve.zip"
    repo_name = "minesweeper-solve"

    print("\n" + "=" * 60)
    print("🚀 STARTING BENCHMARK: /readme")
    print("=" * 60)

    ground_truth_sandbox = tempfile.mkdtemp(prefix="ground_truth_")
    source_zip_path = resolve_zip_path(zip_target)

    with zipfile.ZipFile(source_zip_path, "r") as zip_ref:
        zip_ref.extractall(ground_truth_sandbox)

    verifier = GroundingVerifier(os.path.join(ground_truth_sandbox, repo_name))

    try:
        print("\n⏳ Running agent with default /readme...")
        readme_default = run_readme_and_capture(zip_target, repo_name, mode_flag=None)

        score_default = verifier.evaluate_readme(readme_default)

        print("\n" + "=" * 60)
        print("📊 MINESWEEPER BENCHMARK REPORT")
        print("=" * 60)
        _print_mode_score("default (/readme .)", score_default)
        print("=" * 60)

        assert len(readme_default) > 50, "default /readme failed to generate meaningful text"

    finally:
        shutil.rmtree(ground_truth_sandbox)


@pytest.mark.skip(reason="Run only on-demand if benchmarking needed")
def test_self_macro_benchmark():
    repo_name = "simple-coding-agent"

    print("\n" + "=" * 60)
    print("🚀 STARTING SELF-REPO BENCHMARK: /readme")
    print("=" * 60)

    ground_truth_sandbox = tempfile.mkdtemp(prefix="ground_truth_self_")
    prepare_self_repo_sandbox(ground_truth_sandbox)
    verifier = GroundingVerifier(os.path.join(ground_truth_sandbox, repo_name))

    try:
        print("\n⏳ Running agent with default /readme...")
        readme_default = run_self_readme_and_capture(repo_name, mode_flag=None)

        score_default = verifier.evaluate_readme(readme_default)

        print("\n" + "=" * 60)
        print("📊 SELF-REPO BENCHMARK REPORT (AGENT CODEBASE)")
        print("=" * 60)
        _print_mode_score("default (/readme .)", score_default, include_ref_count=True)
        print("=" * 60)

        assert len(readme_default) > 50, "default /readme failed to generate meaningful text on self-repo"

    finally:
        shutil.rmtree(ground_truth_sandbox)
