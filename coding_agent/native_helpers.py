import os
import subprocess
import re
import platform


# --- NATIVE MAPPER: /readme ---
def get_repo_structure(startpath, max_depth=3):
    ignore_dirs = {'.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__', '.idea', '.vscode', 'dist', 'build'}
    tree_str = ""
    start_sep = startpath.count(os.path.sep)

    for root, dirs, files in os.walk(startpath):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        depth = root.count(os.path.sep) - start_sep

        if depth > max_depth:
            del dirs[:]
            continue

        indent = ' ' * 4 * depth
        tree_str += f"{indent}{os.path.basename(root)}/\n"
        subindent = ' ' * 4 * (depth + 1)
        for f in files:
            if not f.startswith('.'):
                tree_str += f"{subindent}{f}\n"

    return tree_str[:1200]


# --- NATIVE HANDLER: /requirements ---
def generate_requirements_native(target_dir, no_version=False):
    try:
        abs_target_dir = os.path.abspath(os.path.expanduser(target_dir))
        if not os.path.isdir(abs_target_dir):
            return f"Error: Resolved path '{abs_target_dir}' is not a valid system directory."

        is_windows = platform.system() == "Windows"
        pip_bin = None

        for venv_name in [".venv", "venv", "env"]:
            potential_path = os.path.join(abs_target_dir, venv_name)
            if os.path.isdir(potential_path):
                if is_windows:
                    test_pip = os.path.join(potential_path, "Scripts", "pip.exe")
                else:
                    test_pip = os.path.join(potential_path, "bin", "pip")

                if os.path.isfile(test_pip):
                    pip_bin = test_pip
                    break

        if not pip_bin:
            return f"Error: No virtual environment found inside '{abs_target_dir}'."

        final_output_path = os.path.join(abs_target_dir, "requirements.txt")
        print(f"   [Backend] Executing: '{pip_bin}' freeze")

        result = subprocess.run(
            f'"{pip_bin}" freeze', shell=True, capture_output=True, text=True, cwd=abs_target_dir, timeout=15
        )

        if result.returncode != 0:
            return f"Error: Pip execution failed. Stderr: {result.stderr}"

        raw_packages = result.stdout.strip()
        if not raw_packages:
            raw_packages = "# No dependencies found. The virtual environment is empty."
        elif no_version:
            processed_lines = []
            for line in raw_packages.splitlines():
                line_strip = line.strip()
                if not line_strip or line_strip.startswith("#"):
                    processed_lines.append(line_strip)
                    continue
                pkg_name = re.split(r'==|>=|<=| @ ', line_strip)[0].strip()
                if pkg_name:
                    processed_lines.append(pkg_name)
            raw_packages = "\n".join(processed_lines)

        with open(final_output_path, 'w', encoding='utf-8') as f:
            f.write(raw_packages + "\n")

        return f"SUCCESS: Natively generated target file: '{final_output_path}'"

    except Exception as e:
        return f"Error executing native requirements handler: {e}"


def gather_deep_context(startpath):
    """
    Scans for Python files, collects initial file contents,
    and attempts to discover and execute an entry point's --help command.
    """
    ignore_dirs = {'.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__', '.idea', '.vscode', 'dist', 'build'}
    py_files = []

    # 1. Traversal to catch code files
    for root, dirs, files in os.walk(startpath):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for f in files:
            if f.endswith('.py') and not f.startswith('.'):
                py_files.append(os.path.join(root, f))

    code_summary = ""
    entry_point_candidate = None

    for filepath in py_files:
        rel_path = os.path.relpath(filepath, startpath)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                lines = file.readlines()

            # Extract up to 120 lines to capture layout, imports, and docstrings
            content_snippet = "".join(lines[:120])
            code_summary += f"\n--- FILE: {rel_path} ---\n{content_snippet}\n"

            # Look for entry point markers
            file_text = "".join(lines)
            if "__main__" in file_text or "argparse" in file_text or "click" in file_text or "typer" in file_text:
                # Prioritize names that sound like entry configurations
                if not entry_point_candidate or any(kw in f.lower() for kw in ["main", "app", "cli", "run"]):
                    entry_point_candidate = filepath
        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error reading: {e}) ---\n"

    # 2. Query help menu execution
    cli_help = "No explicit entry point with an executable help menu confidently discovered."
    if entry_point_candidate:
        rel_entry = os.path.relpath(entry_point_candidate, startpath)
        try:
            # Safe short-timeout invocation
            result = subprocess.run(
                f'python "{entry_point_candidate}" --help',
                shell=True, capture_output=True, text=True, cwd=startpath, timeout=5
            )
            if result.returncode == 0:
                cli_help = f"Discovered Entry Point: {rel_entry}\nOutput of 'python {rel_entry} --help':\n{result.stdout}"
            else:
                cli_help = f"Discovered Potential Entry Point: {rel_entry}, but '--help' returned a non-zero exit code.\nStderr: {result.stderr}"
        except Exception as e:
            cli_help = f"Attempted to run help diagnostic on {rel_entry} but encountered error: {e}"

    return code_summary, cli_help
