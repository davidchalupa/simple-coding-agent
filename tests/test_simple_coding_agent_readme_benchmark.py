import os
import tempfile
import shutil
import zipfile
from unittest.mock import patch

import simple_coding_agent
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
    """
    # Fixed: Corrected virtualenv patterns to avoid disk quota explosions
    ignore_patterns = shutil.ignore_patterns(
        ".git", ".venv", ".venv*", "venv", "env", "__pycache__",
        ".idea", ".vscode", "dist", "build", ".pytest_cache", "models", "README.md",
    )
    repo_dest = os.path.join(target_dir, "simple-coding-agent")
    # Fixed: Copy from PROJECT_ROOT instead of current working directory
    shutil.copytree(PROJECT_ROOT, repo_dest, ignore=ignore_patterns)
    return repo_dest


def get_generated_readme_content(sandbox_dir, repo_name):
    """Safely extracts the generated README content from expected locations."""
    candidates = [
        os.path.join(sandbox_dir, repo_name, "README.md"),
        os.path.join(sandbox_dir, "README.md")
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return ""


def run_readme_and_capture(zip_file_path, repo_name, mode_flag):
    """Modified runner that returns the README content instead of asserting."""
    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix=f"benchmark_{mode_flag.strip('-')}_")

    source_zip_path = resolve_zip_path(zip_file_path)
    with zipfile.ZipFile(source_zip_path, 'r') as zip_ref:
        zip_ref.extractall(test_sandbox)

    simple_coding_agent.session_cwd = test_sandbox
    simple_coding_agent.FORCE_TESTING = True

    input_queue = [
        # Added guardrail for safety against repetition loops
        "Keep the generated README concise. Do not repeat sections or dummy bash commands.", "/send",
        f"/readme {mode_flag} ./{repo_name}", "/send",
        "Looks good, task complete.", "/send", "/quit"
    ]

    safety_counter = {"calls": 0, "max": 40}

    def mocker(prompt=""):
        safety_counter["calls"] += 1
        if safety_counter["calls"] > safety_counter["max"]: return "/quit"

        p = str(prompt).lower()
        if "allow" in p or "y/n" in p: return "y"

        if input_queue:
            return input_queue.pop(0)

        # Recovery mechanism for truncated JSON payloads
        if "error" in p or "json" in p or "failed" in p:
            return "Your output was truncated or invalid. Please write the file again, but keep it brief and DO NOT repeat lines."

        return "/quit"

    try:
        os.chdir(test_sandbox)
        with patch("builtins.input", side_effect=mocker):
            try:
                simple_coding_agent.main()
            except SystemExit:
                pass

        # Fixed: Used robust helper instead of hardcoded "../README.md"
        return get_generated_readme_content(test_sandbox, repo_name)
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)


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
    original_cwd = os.getcwd()
    ground_truth_sandbox = tempfile.mkdtemp(prefix="ground_truth_")

    # Fixed: Use resolve_zip_path here to avoid crashes on pytest execution
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

        # 4. Print Report (Run pytest with '-s' flag to see this!)
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

        # Soft asserts just to ensure the test passes if generation worked
        assert len(readme_deep) > 50, "--deep failed to generate meaningful text"
        assert len(readme_ast) > 50, "--deep-ast failed to generate meaningful text"

    finally:
        shutil.rmtree(ground_truth_sandbox)


def run_self_readme_and_capture(repo_name, mode_flag):
    """
    Modified runner for the live repository that returns the README content
    for benchmarking instead of asserting immediately.
    """
    original_cwd = os.getcwd()
    test_sandbox = tempfile.mkdtemp(prefix=f"benchmark_self_{mode_flag.strip('-')}_")

    try:
        prepare_self_repo_sandbox(test_sandbox)

        simple_coding_agent.session_cwd = test_sandbox
        simple_coding_agent.FORCE_TESTING = True

        input_queue = [
            "Keep the generated README concise. Do not repeat sections or dummy bash commands.", "/send",
            f"/readme {mode_flag} ./{repo_name}", "/send",
            "Looks good, task complete.", "/send", "/quit"
        ]

        # Bumped max calls to 80 for the larger repository
        safety_counter = {"calls": 0, "max": 80}

        def mocker(prompt=""):
            safety_counter["calls"] += 1
            if safety_counter["calls"] > safety_counter["max"]: return "/quit"

            p = str(prompt).lower()
            if "allow" in p or "y/n" in p: return "y"

            if input_queue:
                return input_queue.pop(0)

            if "error" in p or "json" in p or "failed" in p:
                return "Your output was truncated or invalid. Please write the file again, but keep it brief and DO NOT repeat lines."

            return "/quit"

        os.chdir(test_sandbox)
        with patch("builtins.input", side_effect=mocker):
            try:
                simple_coding_agent.main()
            except SystemExit:
                pass

        # Fixed: Used robust helper
        return get_generated_readme_content(test_sandbox, repo_name)
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(test_sandbox)


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
    original_cwd = os.getcwd()
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

        # 4. Print Report (Run pytest with '-s' flag to see this!)
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

        # Soft asserts to ensure generations actually fired
        assert len(readme_deep) > 50, "--deep failed to generate meaningful text on self-repo"
        assert len(readme_ast) > 50, "--deep-ast failed to generate meaningful text on self-repo"

    finally:
        shutil.rmtree(ground_truth_sandbox)
