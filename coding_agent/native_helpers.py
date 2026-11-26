import ast
from collections import defaultdict
import fnmatch
import os
import platform
import re
import subprocess
import sys


SUPPORTED_SOURCE_EXTENSIONS = {
    ".py",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".kts", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp",
    ".rb", ".php", ".swift", ".sh", ".bash", ".zsh", ".ps1",
    ".sql", ".yaml", ".yml", ".toml", ".json",
}

MANIFEST_FILENAMES = (
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "package.json", "Cargo.toml", "go.mod", "Gemfile", "composer.json",
)

DEFAULT_TREE_IGNORES = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".idea", ".vscode", "dist", "build",
}

DEFAULT_CONTEXT_IGNORES = DEFAULT_TREE_IGNORES | {
    "target", "vendor", "tests", "test", "migrations",
    "alembic", "docs", "site-packages", "test_data",
}

_NON_PY_DECL_RE = re.compile(
    r"^\s*(export\s+)?(async\s+)?(function|class|interface|type|enum|struct|trait|impl|fn|def|mod)\b[^\n{;]+"
)


# --- HELPER: .gitignore Parser ---
def build_ignore_checker(startpath, extra_ignores=None):
    """
    Parses .gitignore and returns a lookup function to check if a file/dir should be ignored.
    Combines hardcoded exclusions with dynamic gitignore rules.
    """
    patterns = set(extra_ignores or ())
    gitignore_path = os.path.join(startpath, ".gitignore")

    if os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.endswith("/"):
                        line = line[:-1]
                    if line.startswith("/"):
                        line = line[1:]
                    line = line.replace("/", os.sep)
                    patterns.add(line)
        except OSError:
            pass

    def is_ignored(name, path=None):
        for p in patterns:
            if name == p or fnmatch.fnmatch(name, p):
                return True
            if path and (
                path == p
                or fnmatch.fnmatch(path, p)
                or path.startswith(p + os.sep)
                or fnmatch.fnmatch(path, p + os.sep + "*")
            ):
                return True
        return False

    return is_ignored


# --- NATIVE MAPPER: Repository Tree ---
def get_repo_structure(startpath, max_depth=3):
    is_ignored = build_ignore_checker(startpath, DEFAULT_TREE_IGNORES)

    tree_str = ""
    start_sep = startpath.count(os.path.sep)

    for root, dirs, files in os.walk(startpath):
        rel_root = os.path.relpath(root, startpath)
        if rel_root == ".":
            rel_root = ""

        dirs[:] = [
            d for d in dirs
            if not is_ignored(d, os.path.join(rel_root, d) if rel_root else d)
        ]

        depth = root.count(os.path.sep) - start_sep
        if depth > max_depth:
            del dirs[:]
            continue

        indent = " " * 4 * depth
        tree_str += f"{indent}{os.path.basename(root)}/\n"
        subindent = " " * 4 * (depth + 1)
        for f in files:
            f_rel = os.path.join(rel_root, f) if rel_root else f
            if not f.startswith(".") and not is_ignored(f, f_rel):
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

        for venv_name in (".venv", "venv", "env"):
            potential_path = os.path.join(abs_target_dir, venv_name)
            if os.path.isdir(potential_path):
                test_pip = (
                    os.path.join(potential_path, "Scripts", "pip.exe")
                    if is_windows
                    else os.path.join(potential_path, "bin", "pip")
                )
                if os.path.isfile(test_pip):
                    pip_bin = test_pip
                    break

        if not pip_bin:
            return f"Error: No virtual environment found inside '{abs_target_dir}'."

        final_output_path = os.path.join(abs_target_dir, "requirements.txt")
        print(f"   [Backend] Executing: '{pip_bin}' freeze")

        result = subprocess.run(
            f'"{pip_bin}" freeze',
            shell=True,
            capture_output=True,
            text=True,
            cwd=abs_target_dir,
            timeout=15,
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
                pkg_name = re.split(r"==|>=|<=| @ ", line_strip)[0].strip()
                if pkg_name:
                    processed_lines.append(pkg_name)
            raw_packages = "\n".join(processed_lines)

        with open(final_output_path, "w", encoding="utf-8") as f:
            f.write(raw_packages + "\n")

        return f"SUCCESS: Natively generated target file: '{final_output_path}'"

    except Exception as e:
        return f"Error executing native requirements handler: {e}"


# --- FILE DISCOVERY & PRIORITIZATION ---
def _gather_project_metadata(startpath):
    """Reads packaging and dependency manifests across Python and other ecosystems."""
    metadata = ""
    for fname in MANIFEST_FILENAMES:
        fpath = os.path.join(startpath, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()[:2000]
                metadata += f"\n--- FILE: {fname} ---\n{content}\n"
            except OSError:
                pass
    return metadata


def _collect_source_files(startpath, base_ignores=None, extensions=None):
    """Collects non-ignored source/config files and measures total byte/char size."""
    ignores = base_ignores if base_ignores is not None else DEFAULT_CONTEXT_IGNORES
    allowed_exts = extensions if extensions is not None else SUPPORTED_SOURCE_EXTENSIONS
    is_ignored = build_ignore_checker(startpath, ignores)

    collected = []
    total_repo_chars = 0

    for root, dirs, files in os.walk(startpath):
        rel_root = os.path.relpath(root, startpath)
        if rel_root == ".":
            rel_root = ""

        dirs[:] = [
            d for d in dirs
            if not is_ignored(d, os.path.join(rel_root, d) if rel_root else d)
        ]

        for f in files:
            f_rel = os.path.join(rel_root, f) if rel_root else f
            ext = os.path.splitext(f)[1].lower()
            if ext in allowed_exts and not f.startswith(".") and not is_ignored(f, f_rel):
                filepath = os.path.join(root, f)
                collected.append(filepath)
                try:
                    total_repo_chars += os.path.getsize(filepath)
                except OSError:
                    pass

    return collected, total_repo_chars


def _collect_py_files(startpath, base_ignores=None):
    """Collects non-ignored .py files under startpath."""
    ignores = base_ignores if base_ignores is not None else DEFAULT_CONTEXT_IGNORES
    return _collect_source_files(startpath, base_ignores=ignores, extensions={".py"})


def _prioritize_files(filepaths, startpath):
    """
    Orders files deterministically by shallow depth, common entry/module stems,
    and relative path.
    """
    generic_entry_stems = {
        "main", "cli", "app", "index", "server", "run", "entry", "__main__",
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

    return sorted(filepaths, key=priority_key)


def _prioritize_py_files(py_files, startpath):
    """Backward-compatible alias for prioritizing files."""
    return _prioritize_files(py_files, startpath)


def _compute_max_context_chars(context_window):
    if context_window:
        return min(int((context_window - 3000) * 3.5), 40000)
    return 25000


# --- PYTHON AST EXTRACTION & BALANCED SUBSYSTEM SUMMARY ---
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
    Groups prioritized files by top-level directory (or '(root)').
    Preserves priority order within each group and puts '(root)' first.
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
    if not outline:
        return []

    entrypoint_names = {"main", "main_loop", "cli", "app"}
    scored = []
    for idx, sym in enumerate(outline):
        if isinstance(sym, dict):
            name = sym.get("name", "")
            kind = sym.get("kind", "")
            doc = (sym.get("doc") or "").strip()
            label = f"{kind} {name}".strip() if (kind or name) else ""
        else:
            label = str(sym).strip()
            if not label:
                continue
            parts = label.split(None, 1)
            kind = parts[0] if len(parts) > 1 and parts[0] in {"class", "def", "async"} else ""
            raw_sig = parts[1] if kind else label
            name = raw_sig.split("(", 1)[0].split(":", 1)[0].strip()
            doc = ""

        if not label:
            continue

        score = 0
        if kind == "class" or label.startswith("class "):
            score += 3
        if name in entrypoint_names or name.startswith("run_"):
            score += 3
        if doc:
            score += 2
        score -= idx * 0.05

        scored.append((score, idx, label))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [label for _, _, label in scored[:limit]]


def build_balanced_ast_summary(py_files, startpath, max_chars):
    """
    Builds a balanced multi-subsystem AST summary preserving docstrings and key
    classes/functions across directories.
    """
    if not py_files:
        return ""

    prioritized = _prioritize_files(py_files, startpath)
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
        carryover = max(0, budget_for_group - used_in_group)

    result = overview_text + "\n" + "\n".join(detail_sections)
    if omitted_files:
        shown = ", ".join(omitted_files[:20])
        more = f" (+{len(omitted_files) - 20} more)" if len(omitted_files) > 20 else ""
        result += f"\n\n[Note: {len(omitted_files)} files had partial/omitted detail due to budget: {shown}{more}]\n"

    if len(result) > max_chars:
        result = result[:max_chars] + "\n[System: Summary capped at max_chars.]\n"

    return result


class CommandVisitor(ast.NodeVisitor):
    """Extracts slash commands and CLI flags with surrounding source context."""

    def __init__(self, source_lines):
        self.commands = {}
        self.flags = {}
        self.source_lines = source_lines

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            val = node.value
            is_cmd = re.match(r"^/[a-zA-Z0-9_-]+$", val)
            is_flag = re.match(r"^--[a-zA-Z0-9_-]+$", val)

            if (is_cmd or is_flag) and hasattr(node, "lineno"):
                start_idx = max(0, node.lineno - 1)
                end_idx = min(len(self.source_lines), node.lineno + 1)
                snippet = " ".join(
                    line.strip() for line in self.source_lines[start_idx:end_idx] if line.strip()
                )
                if is_cmd and val not in self.commands:
                    self.commands[val] = snippet
                elif is_flag and val not in self.flags:
                    self.flags[val] = snippet

        self.generic_visit(node)


def _extract_python_ast_interfaces(py_files):
    """Extracts slash commands and CLI flags from Python files via AST."""
    global_commands = {}
    global_flags = {}

    for filepath in py_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            source_lines = text.splitlines()
            tree = ast.parse(text)
            visitor = CommandVisitor(source_lines)
            visitor.visit(tree)

            for k, v in visitor.commands.items():
                global_commands.setdefault(k, v)
            for k, v in visitor.flags.items():
                global_flags.setdefault(k, v)
        except Exception:
            pass

    if not global_commands and not global_flags:
        return ""

    header = "=== AST DISCOVERED INTERFACES ===\n"
    if global_commands:
        header += (
            "Interactive Chat Macros detected (users type these inside the running interactive prompt, "
            "not as shell arguments):\n"
        )
        for cmd, snippet in sorted(global_commands.items()):
            header += f"  - {cmd}  (Context: `{snippet}`)\n"
        header += "\n"

    if global_flags:
        header += "CLI Flags detected:\n"
        for flag, snippet in sorted(global_flags.items()):
            header += f"  - {flag}  (Context: `{snippet}`)\n"
        header += "\n"

    header += "=================================\n\n"
    return header


# --- NON-PYTHON & RAW EXTRACTION HELPERS ---
def _extract_raw_text_internal(filepaths, startpath, max_chars, max_per_file=None):
    code_summary = ""
    if not filepaths or max_chars <= 0:
        return code_summary

    per_file = max(1000, max_chars // len(filepaths))
    if max_per_file is not None:
        per_file = min(per_file, max_per_file)

    for idx, filepath in enumerate(filepaths):
        if len(code_summary) >= max_chars:
            remaining = [os.path.relpath(p, startpath).replace(os.sep, "/") for p in filepaths[idx:]]
            code_summary += (
                f"\n[System: Context budget reached. Omitted {len(remaining)} lower-priority files: "
                f"{', '.join(remaining[:15])}{'...' if len(remaining) > 15 else ''}]\n"
            )
            break

        rel_path = os.path.relpath(filepath, startpath).replace(os.sep, "/")
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            clean_lines = []
            current_chars = 0
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith(("import ", "from ")):
                    continue
                clean_lines.append(line.rstrip())
                current_chars += len(line)
                if current_chars >= per_file:
                    clean_lines.append("... [Code truncated for length] ...")
                    break

            code_summary += f"\n--- FILE: {rel_path} ---\n{chr(10).join(clean_lines)}\n"
        except Exception as e:
            code_summary += f"\n--- FILE: {rel_path} (Error: {e}) ---\n"

    return code_summary


def _extract_non_py_outline(filepath, max_lines=60):
    """Extracts high-signal declarations and headings from non-Python files."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        return f"  [Error reading file: {exc}]\n"

    outline = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _NON_PY_DECL_RE.match(line) or stripped.startswith(("#!", "## ", "### ")):
            outline.append(f"  {stripped[:140]}")
        if len(outline) >= max_lines:
            outline.append("  ... [Outline trimmed]")
            break

    return "\n".join(outline) + ("\n" if outline else "")


def _build_non_py_summary(non_py_files, startpath, max_chars, raw_mode=False):
    """Summarizes non-Python source/config files via bounded raw snippets or outlines."""
    if not non_py_files or max_chars <= 0:
        return ""

    prioritized = _prioritize_files(non_py_files, startpath)
    per_file_cap = 8000 if raw_mode else None
    sections = ["\n=== NON-PYTHON SOURCE & CONFIG CONTEXT ==="]
    used = len(sections[0]) + 1

    for idx, filepath in enumerate(prioritized):
        if used >= max_chars:
            omitted = len(prioritized) - idx
            sections.append(f"\n[System: Non-Python context budget reached; {omitted} files omitted.]")
            break

        rel_path = os.path.relpath(filepath, startpath).replace(os.sep, "/")
        remaining = max_chars - used

        if raw_mode:
            block = _extract_raw_text_internal(
                [filepath],
                startpath,
                remaining,
                max_per_file=per_file_cap,
            ).strip()
            if block:
                block += "\n"
        else:
            header = f"--- FILE: {rel_path} ---\n"
            body = _extract_non_py_outline(filepath)
            block = header + (body if body.strip() else "  [No top-level declarations extracted]\n")

        if not block:
            continue
        if len(block) > remaining:
            block = block[:remaining].rsplit("\n", 1)[0] + "\n... [Trimmed for budget]\n"

        sections.append(block.rstrip("\n"))
        used += len(block)

    return "\n".join(sections) + "\n"


def _extract_cli_help_internal(
    py_files,
    startpath,
    max_entrypoints=6,
    max_total_chars=8000,
    max_per_entry=2400,
):
    candidates = []
    for filepath in py_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
                if any(x in text for x in ("__main__", "argparse", "click", "typer")):
                    candidates.append(filepath)
        except OSError:
            pass

    captured = []
    seen_help_texts = set()
    total_chars = 0

    for candidate in candidates:
        if len(captured) >= max_entrypoints or total_chars >= max_total_chars:
            break
        rel_entry = os.path.relpath(candidate, startpath).replace(os.sep, "/")
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
            out_lower = result.stdout.lower()
            if result.returncode == 0 and ("usage:" in out_lower or "options:" in out_lower):
                help_text = result.stdout.strip()
                normalized_key = re.sub(r"\s+", " ", help_text)
                if normalized_key in seen_help_texts:
                    continue
                seen_help_texts.add(normalized_key)

                if len(help_text) > max_per_entry:
                    help_text = help_text[:max_per_entry].rsplit("\n", 1)[0] + "\n... [Help truncated]"

                entry_block = f"Discovered Entry Point: {rel_entry}\nOutput:\n{help_text}\n"
                remaining_total = max_total_chars - total_chars
                if len(entry_block) <= remaining_total:
                    captured.append(entry_block)
                    total_chars += len(entry_block)
                elif remaining_total > 200:
                    trimmed = entry_block[:remaining_total].rsplit("\n", 1)[0] + "\n... [Help output truncated]"
                    captured.append(trimmed)
                    total_chars += len(trimmed)
                    break
        except Exception:
            pass

    return "\n---\n".join(captured) if captured else "No explicit CLI --help output captured."


# --- UNIFIED README CONTEXT PIPELINE ---
def gather_readme_context(startpath, context_window=None):
    """
    Unified, language-agnostic README context builder with deep focus by default.
    Preserves high-density Python AST outlines and CLI introspection while supporting
    manifests and non-Python source/config files.
    """
    source_files, total_repo_chars = _collect_source_files(startpath, DEFAULT_CONTEXT_IGNORES)
    prioritized = _prioritize_files(source_files, startpath)

    py_files = [p for p in prioritized if p.lower().endswith(".py")]
    non_py_files = [p for p in prioritized if not p.lower().endswith(".py")]

    metadata_summary = _gather_project_metadata(startpath)
    manifest_lines = "\n".join(
        f"- {os.path.relpath(p, startpath).replace(os.sep, '/')}" for p in prioritized
    )
    file_manifest = f"\n--- PRIORITIZED SOURCE FILES ---\n{manifest_lines}\n"
    ast_interfaces = _extract_python_ast_interfaces(py_files) if py_files else ""

    max_total_chars = _compute_max_context_chars(context_window)
    use_raw_mode = total_repo_chars <= max_total_chars

    if py_files and non_py_files:
        py_budget = int(max_total_chars * 0.70)
        non_py_budget = max(max_total_chars - py_budget, 2000)
    elif py_files:
        py_budget = max_total_chars
        non_py_budget = 0
    else:
        py_budget = 0
        non_py_budget = max_total_chars

    py_section = ""
    if py_files and py_budget > 0:
        if use_raw_mode:
            py_section = "\n=== PYTHON SOURCE CONTEXT ===\n" + _extract_raw_text_internal(
                py_files, startpath, py_budget
            )
        else:
            py_section = "\n=== PYTHON AST CONTEXT ===\n" + build_balanced_ast_summary(
                py_files, startpath, py_budget
            )

    non_py_section = _build_non_py_summary(non_py_files, startpath, non_py_budget, raw_mode=use_raw_mode)

    full_context = ast_interfaces + metadata_summary + file_manifest + py_section + non_py_section
    cli_help = _extract_cli_help_internal(py_files, startpath) if py_files else "No Python CLI entrypoints detected."
    return full_context, cli_help


# --- BACKWARD-COMPATIBLE ENTRYPOINTS ---
def gather_deep_context(startpath, context_window):
    """Backward-compatible wrapper delegating to the unified README context pipeline."""
    return gather_readme_context(startpath, context_window)


def gather_deep_context_ast(startpath, context_window=None):
    """Backward-compatible wrapper delegating to the unified README context pipeline."""
    return gather_readme_context(startpath, context_window)


def _extract_ast_signatures_internal(py_files, startpath, max_chars):
    """Backward-compatible wrapper for balanced AST signature extraction."""
    return build_balanced_ast_summary(py_files, startpath, max_chars)
