import os
import subprocess
import re
import platform
import ast
import fnmatch


# --- HELPER: .gitignore Parser ---
def build_ignore_checker(startpath, extra_ignores=None):
    """
    Parses .gitignore and returns a fast lookup function to check if a file/dir should be ignored.
    Combines hardcoded exclusions with dynamic gitignore rules.
    """
    patterns = set(extra_ignores or [])
    gitignore_path = os.path.join(startpath, '.gitignore')

    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # Ignore comments and empty lines
                    if line and not line.startswith('#'):
                        # Strip trailing slash for directory matching
                        if line.endswith('/'):
                            line = line[:-1]
                        # Remove leading slash for relative matching against the project root
                        if line.startswith('/'):
                            line = line[1:]
                        # Normalize slashes for cross-platform fnmatch compatibility
                        line = line.replace('/', os.sep)
                        patterns.add(line)
        except Exception:
            pass

    def is_ignored(name, path=None):
        for p in patterns:
            # Match direct file/folder name or basic wildcards (*.pyc)
            if name == p or fnmatch.fnmatch(name, p):
                return True
            # Match relative path structures (e.g., config/*.json or frontend/node_modules)
            if path and (path == p or fnmatch.fnmatch(path, p) or path.startswith(p + os.sep) or fnmatch.fnmatch(path,
                                                                                                                 p + os.sep + '*')):
                return True
        return False

    return is_ignored


# --- NATIVE MAPPER: /readme ---
def get_repo_structure(startpath, max_depth=3):
    base_ignores = {'.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__', '.idea', '.vscode', 'dist', 'build'}
    is_ignored = build_ignore_checker(startpath, base_ignores)

    tree_str = ""
    start_sep = startpath.count(os.path.sep)

    for root, dirs, files in os.walk(startpath):
        rel_root = os.path.relpath(root, startpath)
        if rel_root == ".":
            rel_root = ""

        # Filter directories in-place to stop os.walk from entering ignored folders
        dirs[:] = [d for d in dirs if not is_ignored(d, os.path.join(rel_root, d) if rel_root else d)]

        depth = root.count(os.path.sep) - start_sep

        if depth > max_depth:
            del dirs[:]
            continue

        indent = ' ' * 4 * depth
        tree_str += f"{indent}{os.path.basename(root)}/\n"
        subindent = ' ' * 4 * (depth + 1)
        for f in files:
            f_rel = os.path.join(rel_root, f) if rel_root else f
            if not f.startswith('.') and not is_ignored(f, f_rel):
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
    Gathers repository context using a dynamic fair-share character budget.
    Small repos get deep file extraction; large repos get truncated smartly.
    """
    base_ignores = {
        '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
        '.idea', '.vscode', 'dist', 'build', 'tests', 'test', 'migrations',
        'alembic', 'docs', 'site-packages'
    }
    is_ignored = build_ignore_checker(startpath, base_ignores)
    py_files = []

    for root, dirs, files in os.walk(startpath):
        rel_root = os.path.relpath(root, startpath)
        if rel_root == ".":
            rel_root = ""

        dirs[:] = [d for d in dirs if not is_ignored(d, os.path.join(rel_root, d) if rel_root else d)]
        for f in files:
            f_rel = os.path.join(rel_root, f) if rel_root else f
            if f.endswith('.py') and not f.startswith('.') and not is_ignored(f, f_rel):
                py_files.append(os.path.join(root, f))

    code_summary = ""
    candidates = []

    # ~5000 tokens budget. Leaves ~3000 tokens for system prompt & generation headroom
    MAX_TOTAL_CHARS = 20000
    num_files = len(py_files)

    # 1. Calculate Fair-Share Budget per file
    if num_files > 0:
        # Divide budget equally among files, but set a floor (e.g., ~1000 chars / ~30 lines)
        # so large repos still get meaningful signatures before hitting the global circuit breaker.
        char_limit_per_file = max(1000, MAX_TOTAL_CHARS // num_files)
    else:
        char_limit_per_file = 0

    for filepath in py_files:
        # 2. GLOBAL CIRCUIT BREAKER
        if len(code_summary) >= MAX_TOTAL_CHARS:
            code_summary += "\n[System: Repository too large. Code context truncated to safely fit 8k window.]\n"
            break

        rel_path = os.path.relpath(filepath, startpath)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                lines = file.readlines()

            # 3. SMART LINE FILTERING (Character-based)
            clean_lines = []
            current_file_chars = 0

            for line in lines:
                stripped = line.strip()
                if not stripped: continue
                # REMOVED: Stripping `#` because comments/docstrings are vital for README generation!
                if stripped.startswith(("import ", "from ")): continue

                clean_lines.append(line.rstrip())
                current_file_chars += len(line)

                # 4. LOCAL FILE LIMIT
                if current_file_chars >= char_limit_per_file:
                    clean_lines.append("... [Code truncated for length] ...")
                    break

            content_snippet = "\n".join(clean_lines)
            code_summary += f"\n--- FILE: {rel_path} ---\n{content_snippet}\n"

            # Check for CLI candidates
            file_text = "".join(lines)
            if "__main__" in file_text or "argparse" in file_text or "click" in file_text or "typer" in file_text:
                candidates.append(filepath)

        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error reading: {e}) ---\n"

    # CLI Help Extraction (unchanged)
    best_help = ""
    best_entry = None
    for candidate in candidates:
        rel_entry = os.path.relpath(candidate, startpath)
        try:
            result = subprocess.run(
                f'python "{candidate}" --help',
                shell=True, capture_output=True, text=True, cwd=startpath, timeout=5
            )
            if result.returncode == 0 and ("usage:" in result.stdout.lower() or "options:" in result.stdout.lower()):
                if len(result.stdout) > len(best_help):
                    best_help = f"Discovered Entry Point: {rel_entry}\nOutput of 'python {rel_entry} --help':\n{result.stdout}"
                    best_entry = rel_entry
        except Exception:
            pass

    cli_help = best_help if best_entry else "No explicit entry point with an executable help menu confidently discovered."
    return code_summary, cli_help


class CommandVisitor(ast.NodeVisitor):
    """Universal visitor to extract string constants that look like commands/flags."""

    def __init__(self):
        self.commands = set()
        self.flags = set()

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            val = node.value
            if re.match(r"^/[a-zA-Z0-9_-]+$", val):
                self.commands.add(val)
            elif re.match(r"^--[a-zA-Z0-9_-]+$", val):
                self.flags.add(val)
        self.generic_visit(node)


def gather_deep_context_ast(startpath):
    """
    Experimental Smart Dispatcher (Currently bound to --deep-ast for A/B testing).
    Always uses AST to extract CLI flags/commands.
    Routes to Raw Text extraction for small repos, and AST extraction for large repos.
    """
    base_ignores = {
        '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
        '.idea', '.vscode', 'dist', 'build', 'tests', 'test', 'migrations',
        'alembic', 'docs', 'site-packages', 'test_data'
    }
    # Assuming build_ignore_checker is already defined in your file
    is_ignored = build_ignore_checker(startpath, base_ignores)
    py_files = []
    total_repo_chars = 0

    # 1. File Discovery & Sizing
    for root, dirs, files in os.walk(startpath):
        rel_root = os.path.relpath(root, startpath)
        if rel_root == ".":
            rel_root = ""

        dirs[:] = [d for d in dirs if not is_ignored(d, os.path.join(rel_root, d) if rel_root else d)]
        for f in files:
            f_rel = os.path.join(rel_root, f) if rel_root else f
            if f.endswith('.py') and not f.startswith('.') and not is_ignored(f, f_rel):
                filepath = os.path.join(root, f)
                py_files.append(filepath)
                try:
                    total_repo_chars += os.path.getsize(filepath)
                except OSError:
                    pass

    # 2. Universal AST Interface Extraction
    global_commands = set()
    global_flags = set()

    for filepath in py_files:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                tree = ast.parse(file.read())
                visitor = CommandVisitor()
                visitor.visit(tree)
                global_commands.update(visitor.commands)
                global_flags.update(visitor.flags)
        except Exception:
            pass  # Skip syntax errors during broad scan

    command_header = ""
    if global_commands or global_flags:
        command_header = "=== AST DISCOVERED INTERFACES ===\n"
        if global_commands:
            command_header += f"Interactive Commands detected: {', '.join(sorted(global_commands))}\n"
        if global_flags:
            command_header += f"CLI Flags detected: {', '.join(sorted(global_flags))}\n"
        command_header += "=================================\n\n"

    # 3. Smart Dispatcher (Threshold: ~20,000 chars / ~5,000 tokens)
    MAX_TOTAL_CHARS = 20000
    if total_repo_chars <= MAX_TOTAL_CHARS:
        # Re-use the raw text extraction behavior of --deep to ensure a clean A/B test
        code_summary = _extract_raw_text_internal(py_files, startpath, MAX_TOTAL_CHARS)
    else:
        # Use AST signature compression to save the context window
        code_summary = _extract_ast_signatures_internal(py_files, startpath, MAX_TOTAL_CHARS)

    full_context = command_header + code_summary

    # 4. CLI Help Extraction
    cli_help = _extract_cli_help_internal(py_files, startpath)

    return full_context, cli_help


# --- Internal Helpers for the Dispatcher ---

def _extract_raw_text_internal(py_files, startpath, max_chars):
    code_summary = ""
    char_limit_per_file = max(1000, max_chars // len(py_files)) if py_files else 0

    for filepath in py_files:
        if len(code_summary) >= max_chars:
            code_summary += "\n[System: Repository too large. Code context truncated.]\n"
            break
        rel_path = os.path.relpath(filepath, startpath)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            clean_lines = []
            current_chars = 0
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith(("import ", "from ")):
                    continue
                clean_lines.append(line.rstrip())
                current_chars += len(line)
                if current_chars >= char_limit_per_file:
                    clean_lines.append("... [Code truncated for length] ...")
                    break

            code_summary += f"\n--- FILE: {rel_path} ---\n{chr(10).join(clean_lines)}\n"
        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error: {e}) ---\n"

    return code_summary


def _extract_ast_signatures_internal(py_files, startpath, max_chars):
    code_summary = ""

    def get_brief_doc(node):
        doc = ast.get_docstring(node)
        if doc:
            lines = [line.strip() for line in doc.strip().split('\n') if line.strip()]
            return f'"""{lines[0][:100]}"""' if lines else None
        return None

    for filepath in py_files:
        if len(code_summary) >= max_chars:
            code_summary += "\n[System: Repository context truncated.]\n"
            break

        rel_path = os.path.relpath(filepath, startpath)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                file_text = f.read()

            try:
                tree = ast.parse(file_text)
                signatures = []
                has_funcs = False

                mod_doc = get_brief_doc(tree)
                if mod_doc: signatures.append(f'"""Module: {mod_doc}"""')

                for node in tree.body:
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        try:
                            signatures.append(ast.unparse(node))
                        except Exception:
                            pass
                    elif isinstance(node, ast.Assign):
                        try:
                            code_str = ast.unparse(node)
                            if len(code_str) < 100: signatures.append(code_str)
                        except Exception:
                            pass
                    elif isinstance(node, ast.ClassDef):
                        has_funcs = True
                        signatures.append(f"class {node.name}:")
                        if cls_doc := get_brief_doc(node): signatures.append(f"    {cls_doc}")
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                try:
                                    signatures.append(f"    {ast.unparse(item).split(':\\n')[0]}:")
                                except Exception:
                                    signatures.append(f"    def {item.name}(...):")
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        has_funcs = True
                        try:
                            signatures.append(ast.unparse(node).split(':\n')[0] + ":")
                        except Exception:
                            signatures.append(f"def {node.name}(...):")
                        if func_doc := get_brief_doc(node): signatures.append(f"    {func_doc}")

                if not has_funcs:
                    lines = [l for l in file_text.split('\n') if l.strip() and not l.strip().startswith('#')][:25]
                    content = "\n".join(lines)
                else:
                    content = "\n".join(signatures)

            except SyntaxError:
                content = "(Syntax Error parsing file)"

            code_summary += f"\n--- FILE: {rel_path} ---\n{content}\n"
        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error: {e}) ---\n"

    return code_summary


def _extract_cli_help_internal(py_files, startpath):
    candidates = []
    for filepath in py_files:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
                if any(x in text for x in ["__main__", "argparse", "click", "typer"]):
                    candidates.append(filepath)
        except Exception:
            pass

    best_help = ""
    best_entry = None
    for candidate in candidates:
        rel_entry = os.path.relpath(candidate, startpath)
        try:
            result = subprocess.run(
                f'python "{candidate}" --help',
                shell=True, capture_output=True, text=True, cwd=startpath, timeout=5
            )
            if result.returncode == 0 and ("usage:" in result.stdout.lower() or "options:" in result.stdout.lower()):
                if len(result.stdout) > len(best_help):
                    best_help = f"Discovered Entry Point: {rel_entry}\nOutput:\n{result.stdout}"
                    best_entry = rel_entry
        except Exception:
            pass

    return best_help if best_entry else "No explicit CLI --help output captured."
