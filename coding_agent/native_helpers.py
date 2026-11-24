import os
import subprocess
import re
import platform
import ast
import fnmatch
import sys
from collections import defaultdict


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


def _gather_project_metadata(startpath):
    """
    Reads real packaging/dependency metadata so the README prompt has grounded
    facts to work with instead of being forced to invent install instructions
    (e.g. hallucinating a fake git remote URL).
    """
    metadata = ""
    for fname in ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"):
        fpath = os.path.join(startpath, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()[:2000]  # cap length -- this is metadata, not code
                metadata += f"\n--- FILE: {fname} ---\n{content}\n"
            except Exception:
                pass
    return metadata


def _prioritize_py_files(py_files, startpath):
    """
    Orders Python files deterministically using generic structural signals only:
    shallower paths first, common generic entry/module stems ahead of deeper
    nested files, and alphabetical tie-breaking by relative path.
    """
    generic_entry_stems = {
        "main", "cli", "app", "index", "server", "run", "entry", "__main__"
    }

    def priority_key(filepath):
        rel = os.path.relpath(filepath, startpath).replace(os.sep, "/")
        parts = rel.split("/")
        depth = len(parts) - 1
        stem = os.path.splitext(parts[-1])[0].lower()

        if depth == 0 and stem in generic_entry_stems:
            tier = 0
        elif depth == 0:
            tier = 1
        elif depth == 1 and stem in generic_entry_stems:
            tier = 2
        else:
            tier = 3 + depth

        return (tier, rel)

    return sorted(py_files, key=priority_key)


def _first_docstring_line(node, max_len=120):
    """Extracts a concise first line from an AST node's docstring, if present."""
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    first_line = doc.splitlines()[0].strip()
    if len(first_line) > max_len:
        return first_line[: max_len - 3].rstrip() + "..."
    return first_line


def _format_signature(node):
    """Formats a compact function/async-function signature."""
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    args = [a.arg for a in node.args.args]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    elif node.args.kwonlyargs:
        args.append("*")
    for kw in node.args.kwonlyargs:
        args.append(kw.arg)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    return f"{prefix}{node.name}({', '.join(args)}):"


def _extract_ast_outline(filepath, include_method_docstrings=True):
    """
    Extracts module docstring, top-level classes (with docstrings and method
    signatures/docstrings), and top-level functions (with signatures/docstrings).
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as exc:
        return f"  [SyntaxError parsing AST: {exc}]\n"
    except OSError as exc:
        return f"  [Error reading file: {exc}]\n"

    lines_out = []
    mod_doc = _first_docstring_line(tree)
    if mod_doc:
        lines_out.append(f'  """{mod_doc}"""')

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            cls_doc = _first_docstring_line(node)
            cls_header = f"class {node.name}:"
            if cls_doc:
                cls_header += f'  # "{cls_doc}"'
            lines_out.append(f"  {cls_header}")

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = _format_signature(item)
                    mdoc = _first_docstring_line(item) if include_method_docstrings else ""
                    if mdoc:
                        lines_out.append(f'    {sig}  # "{mdoc}"')
                    else:
                        lines_out.append(f"    {sig}")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _format_signature(node)
            fdoc = _first_docstring_line(node)
            if fdoc:
                lines_out.append(f'  {sig}  # "{fdoc}"')
            else:
                lines_out.append(f"  {sig}")

    return "\n".join(lines_out) + ("\n" if lines_out else "")


def _group_files_by_subsystem(py_files, startpath):
    """
    Groups prioritized Python files by top-level directory (or '(root)').
    Preserves incoming priority order within each group and orders groups
    with '(root)' first, followed by alphabetical directory names.
    """
    grouped = defaultdict(list)
    for filepath in py_files:
        rel = os.path.relpath(filepath, startpath).replace(os.sep, "/")
        parts = rel.split("/")
        group = "(root)" if len(parts) == 1 else parts[0]
        grouped[group].append((rel, filepath))

    ordered_groups = sorted(
        grouped.keys(),
        key=lambda g: (0 if g == "(root)" else 1, g.lower()),
    )
    return [(g, grouped[g]) for g in ordered_groups]


def _select_key_symbols(outline, limit=6):
    """
    Selects top-level symbols for subsystem overview previews, preferring
    public symbols (not starting with '_') ahead of private ones.
    """
    public_symbols = []
    private_symbols = []

    for line in outline.splitlines():
        s = line.strip()
        if s.startswith("class ") or s.startswith("def ") or s.startswith("async def "):
            name_part = s.split("(", 1)[0].rstrip(":")
            parts = name_part.split(" ", 1)
            symbol_name = parts[1].strip() if len(parts) > 1 else name_part
            target_list = private_symbols if symbol_name.startswith("_") else public_symbols
            if name_part not in target_list:
                target_list.append(name_part)

    ordered = public_symbols + [sym for sym in private_symbols if sym not in public_symbols]
    return ordered[:limit]


def build_balanced_ast_summary(py_files, startpath, max_chars):
    """
    Builds a balanced multi-subsystem summary preserving docstrings and key
    classes/functions across directories rather than letting one large group or
    single file crowd out the rest.
    """
    if not py_files:
        return ""

    prioritized = _prioritize_py_files(py_files, startpath)
    groups = _group_files_by_subsystem(prioritized, startpath)

    overview_budget = min(int(max_chars * 0.20), 6000)
    detail_budget = max(max_chars - overview_budget, 4000)

    overview_lines = ["=== SUBSYSTEM OVERVIEW ==="]
    group_entries = []

    for group_name, items in groups:
        group_symbols = []
        for _, fp in items:
            outline = _extract_ast_outline(fp, include_method_docstrings=False)
            for sym in _select_key_symbols(outline, limit=6):
                if sym not in group_symbols:
                    group_symbols.append(sym)
                if len(group_symbols) >= 6:
                    break
            if len(group_symbols) >= 6:
                break

        summary_line = f"- {group_name} ({len(items)} files)"
        if group_symbols:
            preview = ", ".join(group_symbols[:6])
            summary_line += f" [key: {preview}]"
        overview_lines.append(summary_line)
        group_entries.append((group_name, items))

    overview_text = "\n".join(overview_lines)
    if len(overview_text) > overview_budget:
        overview_text = overview_text[:overview_budget].rsplit("\n", 1)[0] + "\n... [Overview trimmed]"

    num_groups = len(group_entries)
    per_group_share = detail_budget // max(1, num_groups)
    carryover = 0
    detail_sections = []
    omitted_files = []

    for group_name, items in group_entries:
        budget_for_group = per_group_share + carryover
        used_in_group = 0
        section_lines = [f"\n=== SUBSYSTEM: {group_name} ==="]
        used_in_group += len(section_lines[0]) + 1

        for item_idx, (rel, fp) in enumerate(items):
            header = f"--- FILE: {rel} ---\n"
            body = _extract_ast_outline(fp, include_method_docstrings=True)
            block = header + body

            if used_in_group + len(block) <= budget_for_group:
                section_lines.append(block.rstrip("\n"))
                used_in_group += len(block)
            else:
                remaining_space = budget_for_group - used_in_group - len(header) - 40
                if remaining_space > 120:
                    trimmed = body[:remaining_space].rsplit("\n", 1)[0]
                    section_lines.append(header + trimmed + "\n  ... [Trimmed for subsystem budget]")
                    used_in_group += len(header) + len(trimmed) + 36
                omitted_files.extend(r for r, _ in items[item_idx:])
                break

        detail_sections.append("\n".join(section_lines))
        unused = max(0, budget_for_group - used_in_group)
        carryover = unused

    result = overview_text + "\n" + "\n".join(detail_sections)
    if omitted_files:
        shown = ", ".join(omitted_files[:20])
        more = f" (+{len(omitted_files) - 20} more)" if len(omitted_files) > 20 else ""
        result += f"\n\n[Note: {len(omitted_files)} files had partial/omitted detail due to budget: {shown}{more}]\n"

    if len(result) > max_chars:
        result = result[:max_chars] + "\n[System: Summary capped at max_chars.]\n"

    return result


def _collect_py_files(startpath, base_ignores):
    """Collects non-ignored .py files under startpath."""
    is_ignored = build_ignore_checker(startpath, base_ignores)
    py_files = []
    total_repo_chars = 0

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

    return py_files, total_repo_chars


def gather_deep_context(startpath, context_window):
    """
    Gathers repository context using a dynamic fair-share character budget.
    Small repos get deep file extraction; large repos use a balanced multi-subsystem summary.
    """
    base_ignores = {
        '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
        '.idea', '.vscode', 'dist', 'build', 'tests', 'test', 'migrations',
        'alembic', 'docs', 'site-packages'
    }
    py_files, total_repo_chars = _collect_py_files(startpath, base_ignores)

    code_summary = _gather_project_metadata(startpath)
    py_files = _prioritize_py_files(py_files, startpath)
    file_manifest = "\n".join(f"- {os.path.relpath(p, startpath)}" for p in py_files)
    code_summary += f"\n--- PRIORITIZED PYTHON FILES ---\n{file_manifest}\n"

    # ToDo: the lower bound of 40000 should be changed to be model-specific
    MAX_TOTAL_CHARS = min(int((context_window - 3000) * 3.5), 40000)

    if total_repo_chars <= MAX_TOTAL_CHARS:
        code_summary += "\n" + _extract_raw_text_internal(py_files, startpath, MAX_TOTAL_CHARS)
    else:
        code_summary += "\n" + build_balanced_ast_summary(py_files, startpath, MAX_TOTAL_CHARS)

    cli_help = _extract_cli_help_internal(py_files, startpath)
    return code_summary, cli_help


class CommandVisitor(ast.NodeVisitor):
    """Universal visitor to extract string constants and their surrounding code context."""

    def __init__(self, source_lines):
        self.commands = {}  # Map: command -> code snippet
        self.flags = {}  # Map: flag -> code snippet
        self.source_lines = source_lines

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            val = node.value
            is_cmd = re.match(r"^/[a-zA-Z0-9_-]+$", val)
            is_flag = re.match(r"^--[a-zA-Z0-9_-]+$", val)

            # If it's a command/flag and we know its line number
            if (is_cmd or is_flag) and hasattr(node, 'lineno'):
                # Grab the exact line and one line of lookahead for context
                start_idx = max(0, node.lineno - 1)
                end_idx = min(len(self.source_lines), node.lineno + 1)
                snippet = " ".join([line.strip() for line in self.source_lines[start_idx:end_idx] if line.strip()])

                if is_cmd and val not in self.commands:
                    self.commands[val] = snippet
                elif is_flag and val not in self.flags:
                    self.flags[val] = snippet

        self.generic_visit(node)


def gather_deep_context_ast(startpath, context_window=None):
    """
    Experimental Smart Dispatcher (Currently bound to --deep-ast for A/B testing).
    Always uses AST to extract CLI flags/commands.
    Routes to Raw Text extraction for small repos, and balanced AST summary for large repos.
    """
    base_ignores = {
        '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
        '.idea', '.vscode', 'dist', 'build', 'tests', 'test', 'migrations',
        'alembic', 'docs', 'site-packages', 'test_data'
    }
    py_files, total_repo_chars = _collect_py_files(startpath, base_ignores)

    # 2. Universal AST Interface Extraction
    global_commands = {}
    global_flags = {}

    for filepath in py_files:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                text = file.read()
                source_lines = text.splitlines()
                tree = ast.parse(text)

                visitor = CommandVisitor(source_lines)
                visitor.visit(tree)

                for k, v in visitor.commands.items():
                    if k not in global_commands:
                        global_commands[k] = v
                for k, v in visitor.flags.items():
                    if k not in global_flags:
                        global_flags[k] = v
        except Exception:
            pass  # Skip syntax errors during broad scan

    command_header = ""
    if global_commands or global_flags:
        command_header = "=== AST DISCOVERED INTERFACES ===\n"
        command_header += "CRITICAL ARCHITECTURE NOTE: This application operates via an interactive chat loop. Users must start the application first, and THEN type these commands into the prompt.\n\n"

        if global_commands:
            command_header += "Interactive Chat Macros detected:\n"
            for cmd, snippet in sorted(global_commands.items()):
                command_header += f"  - {cmd}  (Found in code context: `{snippet}`)\n"
            command_header += "\nUsage rule: Do NOT document these as command-line arguments (e.g., 'python app.py /command' is WRONG). Document that the user types them into the running app.\n\n"

        if global_flags:
            command_header += "Standard CLI Flags detected:\n"
            for flag, snippet in sorted(global_flags.items()):
                command_header += f"  - {flag}  (Found in code context: `{snippet}`)\n"

        command_header += "=================================\n\n"

    # Ground the prompt with real packaging metadata, same as gather_deep_context.
    metadata_summary = _gather_project_metadata(startpath)

    # 3. Smart Dispatcher
    py_files = _prioritize_py_files(py_files, startpath)
    if context_window:
        MAX_TOTAL_CHARS = min(int((context_window - 3000) * 3.5), 40000)
    else:
        MAX_TOTAL_CHARS = 25000

    if total_repo_chars <= MAX_TOTAL_CHARS:
        code_summary = _extract_raw_text_internal(py_files, startpath, MAX_TOTAL_CHARS)
    else:
        code_summary = build_balanced_ast_summary(py_files, startpath, MAX_TOTAL_CHARS)

    # print("======")
    # print("code_summary")
    # print("======")
    # print(code_summary)

    full_context = command_header + metadata_summary + code_summary

    # 4. CLI Help Extraction
    cli_help = _extract_cli_help_internal(py_files, startpath)

    return full_context, cli_help


# --- Internal Helpers for the Dispatcher ---

def _extract_raw_text_internal(py_files, startpath, max_chars):
    code_summary = ""
    char_limit_per_file = max(1000, max_chars // len(py_files)) if py_files else 0

    for idx, filepath in enumerate(py_files):
        if len(code_summary) >= max_chars:
            remaining = [os.path.relpath(p, startpath) for p in py_files[idx:]]
            code_summary += (
                f"\n[System: Context budget reached. Omitted {len(remaining)} lower-priority files: "
                f"{', '.join(remaining[:15])}{'...' if len(remaining) > 15 else ''}]\n"
            )
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
    """Kept for backward compatibility with any external callers."""
    return build_balanced_ast_summary(py_files, startpath, max_chars)


def _extract_cli_help_internal(py_files, startpath, max_entrypoints=3, max_total_chars=6000):
    candidates = []
    for filepath in py_files:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
                if any(x in text for x in ["__main__", "argparse", "click", "typer"]):
                    candidates.append(filepath)
        except Exception:
            pass

    captured = []
    total_chars = 0

    for candidate in candidates:
        if len(captured) >= max_entrypoints:
            break
        rel_entry = os.path.relpath(candidate, startpath)
        try:
            result = subprocess.run(
                [sys.executable, os.path.abspath(candidate), "--help"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                cwd=startpath,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and ("usage:" in result.stdout.lower() or "options:" in result.stdout.lower()):
                help_text = result.stdout.strip()
                entry_block = f"Discovered Entry Point: {rel_entry}\nOutput:\n{help_text}\n"
                if total_chars + len(entry_block) <= max_total_chars:
                    captured.append(entry_block)
                    total_chars += len(entry_block)
                else:
                    remaining = max_total_chars - total_chars
                    if remaining > 200:
                        captured.append(entry_block[:remaining] + "\n... [Help output truncated]")
                    break
        except Exception:
            pass

    return "\n---\n".join(captured) if captured else "No explicit CLI --help output captured."
