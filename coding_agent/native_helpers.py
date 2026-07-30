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
    Gathers repository context using smart line filtering.
    Includes a global character budget to prevent context window overflow.
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

    for filepath in py_files:
        # CIRCUIT BREAKER: Stop adding files if we hit the limit
        if len(code_summary) >= MAX_TOTAL_CHARS:
            code_summary += "\n[System: Repository too large. Code context truncated to safely fit 8k window.]\n"
            break

        rel_path = os.path.relpath(filepath, startpath)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                lines = file.readlines()

            # --- SMART LINE FILTERING ---
            clean_lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped: continue
                if stripped.startswith(("#", "import ", "from ")): continue

                clean_lines.append(line.rstrip())
                if len(clean_lines) >= 35:
                    break

            content_snippet = "\n".join(clean_lines)
            code_summary += f"\n--- FILE: {rel_path} ---\n{content_snippet}\n"

            file_text = "".join(lines)
            if "__main__" in file_text or "argparse" in file_text or "click" in file_text or "typer" in file_text:
                candidates.append(filepath)
        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error reading: {e}) ---\n"

    # CLI Help Extraction
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


def gather_deep_context_ast(startpath):
    """
    Gathers repository context using AST.
    Crucially, it uses an AST Visitor to discover string constants that represent
    CLI flags or interactive commands, overcoming the 'blind spot' of pure signature extraction.
    """
    base_ignores = {
        '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
        '.idea', '.vscode', 'dist', 'build', 'tests', 'test', 'migrations',
        'alembic', 'docs', 'site-packages', 'test_data'
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
    MAX_TOTAL_CHARS = 20000

    # AST Walker to find hidden commands inside procedural code (like your main loop)
    class CommandVisitor(ast.NodeVisitor):
        def __init__(self):
            self.commands = set()
            self.flags = set()

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                val = node.value
                # Precisely capture slash commands (e.g., "/readme") and flags (e.g., "--allow-patch")
                if re.match(r"^/[a-zA-Z0-9_-]+$", val):
                    self.commands.add(val)
                elif re.match(r"^--[a-zA-Z0-9_-]+$", val):
                    self.flags.add(val)
            self.generic_visit(node)

    global_commands = set()
    global_flags = set()

    def get_brief_doc(node):
        doc = ast.get_docstring(node)
        if doc:
            lines = [line.strip() for line in doc.strip().split('\n') if line.strip()]
            return f'"""{lines[0][:100]}"""' if lines else None
        return None

    for filepath in py_files:
        if len(code_summary) >= MAX_TOTAL_CHARS:
            code_summary += "\n[System: Repository context truncated to safely fit 8k window.]\n"
            break

        rel_path = os.path.relpath(filepath, startpath)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                file_text = file.read()

            try:
                tree = ast.parse(file_text)
                signatures = []
                has_functions_or_classes = False

                # 1. Extract embedded commands and flags
                visitor = CommandVisitor()
                visitor.visit(tree)
                global_commands.update(visitor.commands)
                global_flags.update(visitor.flags)

                mod_doc = get_brief_doc(tree)
                if mod_doc:
                    signatures.append(f'"""Module Doc: {mod_doc}"""')

                # 2. Iterate through top-level nodes
                for node in tree.body:
                    # Capture Imports (Tech Stack)
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        try:
                            signatures.append(ast.unparse(node))
                        except Exception:
                            pass  # Fallback for older python versions

                    # Capture Top-Level Constants/Configs
                    elif isinstance(node, ast.Assign):
                        try:
                            # Only grab simple assignments (e.g., CONST = 5)
                            code_str = ast.unparse(node)
                            if len(code_str) < 100:  # Prevent huge dicts from eating budget
                                signatures.append(code_str)
                        except Exception:
                            pass

                    # Capture Classes
                    elif isinstance(node, ast.ClassDef):
                        has_functions_or_classes = True
                        signatures.append(f"class {node.name}:")
                        cls_doc = get_brief_doc(node)
                        if cls_doc:
                            signatures.append(f"    {cls_doc}")

                        # Grab methods inside the class
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                try:
                                    # Use ast.unparse to get full signature WITH arguments
                                    sig_str = ast.unparse(item).split(':\n')[0] + ":"
                                    signatures.append(f"    {sig_str}")
                                except Exception:
                                    signatures.append(f"    def {item.name}(...):")

                                func_doc = get_brief_doc(item)
                                if func_doc:
                                    signatures.append(f"        {func_doc}")

                    # Capture Functions
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        has_functions_or_classes = True
                        try:
                            sig_str = ast.unparse(node).split(':\n')[0] + ":"
                            signatures.append(sig_str)
                        except Exception:
                            signatures.append(f"def {node.name}(...):")

                        func_doc = get_brief_doc(node)
                        if func_doc:
                            signatures.append(f"    {func_doc}")

                # 3. HYBRID FALLBACK: If the file is just a script, grab the top 25 lines
                if not has_functions_or_classes:
                    clean_script_lines = [
                                             line for line in file_text.split('\n')
                                             if line.strip() and not line.strip().startswith('#')
                                         ][:25]
                    content_snippet = "\n".join(clean_script_lines)
                else:
                    content_snippet = "\n".join(signatures)

            except SyntaxError:
                content_snippet = "(Syntax Error parsing file)"

            code_summary += f"\n--- FILE: {rel_path} ---\n{content_snippet}\n"

            if "__main__" in file_text or "argparse" in file_text or "click" in file_text or "typer" in file_text:
                candidates.append(filepath)

        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error reading: {e}) ---\n"

    # Prepend the dynamically discovered commands to the context so the LLM knows what to document
    command_header = ""
    if global_commands or global_flags:
        command_header = "=== AST DISCOVERED INTERFACES ===\n"
        if global_commands:
            command_header += f"Interactive Commands detected: {', '.join(sorted(global_commands))}\n"
        if global_flags:
            command_header += f"CLI Flags detected: {', '.join(sorted(global_flags))}\n"
        command_header += "=================================\n\n"

    full_context = command_header + code_summary

    # CLI Help Extraction
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

    cli_help = best_help if best_entry else "No explicit CLI --help output captured."
    return full_context, cli_help
