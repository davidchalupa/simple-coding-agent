import os
import tempfile
import shutil
import zipfile
import pytest

from tests.test_utils.test_runner import run_automated_coding_task_test
from tests.test_utils.grounding_verifier import GroundingVerifier


# Project root directory (parent of the tests/ folder)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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


def run_readme_and_capture(zip_file_path, repo_name, mode_flag):
    """Executes the agent via unified runner and captures generated README content."""
    captured = {}

    def capture_readme_content(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            captured["content"] = f.read()

    input_queue = [
        "Keep the generated README concise. Do not repeat sections or dummy bash commands.", "/send",
        # Fix: The runner already `cd`s into repo_name, so we target "." instead of "./repo_name"
        f"/readme {mode_flag} .", "/send",
        "Looks good, task complete.", "/send", "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        zip_file_path=resolve_zip_path(zip_file_path),
        repo_name=repo_name,
        expected_file="README.md",
        max_calls_limit=40,
        custom_file_validator=capture_readme_content,
    )

    return captured.get("content", "")


def run_self_readme_and_capture(repo_name, mode_flag):
    """Executes the agent against the live repository via unified runner and captures README content."""
    captured = {}

    def capture_readme_content(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            captured["content"] = f.read()

    input_queue = [
        "Keep the generated README concise. Do not repeat sections or dummy bash commands.", "/send",
        # Fix: The runner already `cd`s into repo_name, so we target "." instead of "./repo_name"
        f"/readme {mode_flag} .", "/send",
        "Looks good, task complete.", "/send", "/quit"
    ]

    run_automated_coding_task_test(
        input_queue=input_queue,
        setup_sandbox_hook=prepare_self_repo_sandbox,
        repo_name=repo_name,
        expected_file="README.md",
        max_calls_limit=80,
        custom_file_validator=capture_readme_content,
    )

    return captured.get("content", "")


@pytest.mark.skip(reason="Run only on-demand if benchmarking needed")
def test_minesweeper_macro_benchmark():
    """
    Pilots the benchmark comparison between --deep and --deep-ast
    on the Minesweeper repository (generating both READMEs in a sandbox and comparing
    their quality with GroundingVerifier).
    """
    zip_target = "test_data/minesweeper-solve.zip"
    repo_name = "minesweeper-solve"

    print("\n" + "=" * 60)
    print("🚀 STARTING BENCHMARK: --deep vs --deep-ast")
    print("=" * 60)

    # 1. Setup Ground Truth Verifier
    ground_truth_sandbox = tempfile.mkdtemp(prefix="ground_truth_")
    source_zip_path = resolve_zip_path(zip_target)

    with zipfile.ZipFile(source_zip_path, 'r') as zip_ref:
        zip_ref.extractall(ground_truth_sandbox)

    verifier = GroundingVerifier(os.path.join(ground_truth_sandbox, repo_name))

    try:
        # 2. Run both modes and capture output
        print("\n⏳ Running agent with --deep...")
        readme_deep = run_readme_and_capture(zip_target, repo_name, "--deep")

        print("⏳ Running agent with --deep-ast...")
        readme_ast = run_readme_and_capture(zip_target, repo_name, "--deep-ast")

        # 3. Evaluate
        score_deep = verifier.evaluate_readme(readme_deep)
        score_ast = verifier.evaluate_readme(readme_ast)

        # 4. Print Report
        print("\n" + "=" * 60)
        print("📊 MINESWEEPER BENCHMARK REPORT")
        print("=" * 60)

        print("Mode [--deep]:")
        print(f"  • Groundedness Precision: {score_deep['groundedness_score']}%")
        print(f"  • Info Density:           {score_deep['density_score']}")
        print(f"  • Suspected Hallucinations:\n    {score_deep['hallucination_candidates']}")

        print("\nMode [--deep-ast]:")
        print(f"  • Groundedness Precision: {score_ast['groundedness_score']}%")
        print(f"  • Info Density:           {score_ast['density_score']}")
        print(f"  • Suspected Hallucinations:\n    {score_ast['hallucination_candidates']}")
        print("=" * 60)

        assert len(readme_deep) > 50, "--deep failed to generate meaningful text"
        assert len(readme_ast) > 50, "--deep-ast failed to generate meaningful text"

    finally:
        shutil.rmtree(ground_truth_sandbox)


@pytest.mark.skip(reason="Run only on-demand if benchmarking needed")
def test_self_macro_benchmark():
    """
    Pilots the benchmark comparison between --deep and --deep-ast
    on the live Simple Coding Agent repository to measure performance on a larger codebase.
    """
    repo_name = "simple-coding-agent"

    print("\n" + "=" * 60)
    print("🚀 STARTING SELF-REPO BENCHMARK: --deep vs --deep-ast")
    print("=" * 60)

    # 1. Setup Ground Truth Verifier from the live codebase
    ground_truth_sandbox = tempfile.mkdtemp(prefix="ground_truth_self_")

    prepare_self_repo_sandbox(ground_truth_sandbox)
    verifier = GroundingVerifier(os.path.join(ground_truth_sandbox, repo_name))

    try:
        # 2. Run both modes and capture output
        print("\n⏳ Running agent with --deep (Line-by-Line Regex)...")
        readme_deep = run_self_readme_and_capture(repo_name, "--deep")

        print("⏳ Running agent with --deep-ast (AST Structure/Signatures)...")
        readme_ast = run_self_readme_and_capture(repo_name, "--deep-ast")

        # 3. Evaluate using the verifier
        score_deep = verifier.evaluate_readme(readme_deep)
        score_ast = verifier.evaluate_readme(readme_ast)

        # 4. Print Report
        print("\n" + "=" * 60)
        print("📊 SELF-REPO BENCHMARK REPORT (AGENT CODEBASE)")
        print("=" * 60)

        print("Mode [--deep]:")
        print(f"  • Groundedness Precision: {score_deep['groundedness_score']}%")
        print(f"  • Info Density:           {score_deep['density_score']}")
        print(f"  • Total Code References:  {score_deep['total_code_references']}")
        print(f"  • Suspected Hallucinations:\n    {score_deep['hallucination_candidates']}")

        print("\nMode [--deep-ast]:")
        print(f"  • Groundedness Precision: {score_ast['groundedness_score']}%")
        print(f"  • Info Density:           {score_ast['density_score']}")
        print(f"  • Total Code References:  {score_ast['total_code_references']}")
        print(f"  • Suspected Hallucinations:\n    {score_ast['hallucination_candidates']}")
        print("=" * 60)

        assert len(readme_deep) > 50, "--deep failed to generate meaningful text on self-repo"
        assert len(readme_ast) > 50, "--deep-ast failed to generate meaningful text on self-repo"

    finally:
        shutil.rmtree(ground_truth_sandbox)
