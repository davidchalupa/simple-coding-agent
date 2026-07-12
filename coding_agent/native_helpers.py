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
    ignore_dirs = {'.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__', '.idea', '.vscode', 'dist', 'build'}
    py_files = []

    for root, dirs, files in os.walk(startpath):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for f in files:
            if f.endswith('.py') and not f.startswith('.'):
                py_files.append(os.path.join(root, f))

    code_summary = ""
    candidates = []

    for filepath in py_files:
        rel_path = os.path.relpath(filepath, startpath)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                lines = file.readlines()

            content_snippet = "".join(lines[:120])
            code_summary += f"\n--- FILE: {rel_path} ---\n{content_snippet}\n"

            file_text = "".join(lines)
            # Tag any file that looks like it handles execution
            if "__main__" in file_text or "argparse" in file_text or "click" in file_text or "typer" in file_text:
                candidates.append(filepath)
        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error reading: {e}) ---\n"

    # Actively run --help on all candidates to catch the true CLI output
    best_help = ""
    best_entry = None

    for candidate in candidates:
        rel_entry = os.path.relpath(candidate, startpath)
        try:
            result = subprocess.run(
                f'python "{candidate}" --help',
                shell=True, capture_output=True, text=True, cwd=startpath, timeout=5
            )
            # A valid argparse help block always returns standard exit code 0 and usage headers
            if result.returncode == 0 and ("usage:" in result.stdout.lower() or "options:" in result.stdout.lower()):
                if len(result.stdout) > len(best_help):
                    best_help = f"Discovered Entry Point: {rel_entry}\nOutput of 'python {rel_entry} --help':\n{result.stdout}"
                    best_entry = rel_entry
        except Exception:
            pass

    cli_help = best_help if best_entry else "No explicit entry point with an executable help menu confidently discovered."
    return code_summary, cli_help
