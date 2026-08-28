import os
import subprocess
import ast
import re
import difflib

from pathlib import Path


# 2. Tool Definitions
def read_file(filepath, start_line=1, max_lines=100):
    """Reads a file with strict pagination to prevent context window exhaustion.
    Output includes 1-indexed line numbers so the model can accurately reference
    exact line ranges later (e.g. for replace_lines)."""
    try:
        if not os.path.isfile(filepath):
            return f"Error: '{filepath}' is not a file."

        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        total_lines = len(lines)
        start_idx = max(0, int(start_line) - 1)
        end_idx = start_idx + max(1, int(max_lines))

        selected_lines = lines[start_idx:end_idx]
        numbered_lines = [
            f"{start_idx + i + 1:5d}\t{line.rstrip(chr(10))}"
            for i, line in enumerate(selected_lines)
        ]
        content = "\n".join(numbered_lines)

        if total_lines > end_idx:
            content += f"\n\n... [TRUNCATED: Lines {end_idx + 1} to {total_lines} remain. Use read_file with start_line={end_idx + 1} if needed] ..."

        return content
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(filepath, content):
    """Creates or completely overwrites a file."""
    try:
        if not content.strip():
            return "Error: Refused to write an empty file. If you meant to stop, just announce completion."
        os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) else None
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing file: {e}"


def append_file(filepath, content):
    """Appends content to the end of an existing file. Perfect for building large files safely."""
    try:
        if not content.strip():
            return "Error: Refused to append empty whitespace. If the file is complete, announce completion and stop."
        if not os.path.exists(filepath):
            return f"Error: File '{filepath}' does not exist. Use write_file to initialize it first."
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully appended content to {filepath}"
    except Exception as e:
        return f"Error appending to file: {e}"


# def patch_file(filepath, search_text, replace_text):
#     """Surgically replaces a specific block of text inside a file."""
#     try:
#         if not os.path.exists(filepath):
#             return f"Error: File '{filepath}' does not exist."
#
#         with open(filepath, 'r', encoding='utf-8') as f:
#             content = f.read()
#
#         if search_text not in content:
#             return "Error: The exact 'search_text' block was not found in the file. Patch failed."
#
#         updated_content = content.replace(search_text, replace_text, 1)  # Only replace first match for safety
#
#         with open(filepath, 'w', encoding='utf-8') as f:
#             f.write(updated_content)
#
#         return f"Successfully patched {filepath}."
#     except Exception as e:
#         return f"Error patching file: {e}"


def run_cmd(command):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
        output = result.stdout
        if result.stderr:
            output += f"\nErrors:\n{result.stderr}"
        return output[:1200]
    except Exception as e:
        return f"Error executing command: {e}"


def extract_code_blocks(source_filepath, target_filepath, block_names, wrap_in_class=None):
    """
    Deterministically extracts exact source code blocks from the source file
    and writes them to the target file, including global imports.
    """
    import os
    import ast
    import textwrap

    try:
        with open(source_filepath, 'r', encoding='utf-8') as f:
            source = f.read()

        tree = ast.parse(source)

        # 1. Grab all global imports from the original file
        imports = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                segment = ast.get_source_segment(source, node)
                if segment:
                    imports.append(segment)

        # 2. Extract and dedent requested blocks
        extracted_blocks = []
        found_blocks = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in block_names:
                    segment = ast.get_source_segment(source, node)
                    if segment:
                        # Strip original class/nested indentation
                        dedented_segment = textwrap.dedent(segment)

                        if wrap_in_class and not isinstance(node, ast.ClassDef):
                            # Indent 4 spaces under the new class header
                            indented_lines = ["    " + line if line.strip() else line
                                              for line in dedented_segment.splitlines()]
                            dedented_segment = "\n".join(indented_lines)

                        extracted_blocks.append(dedented_segment)
                        found_blocks += 1

        if found_blocks == 0:
            return f"Error: None of the requested blocks ({block_names}) were found."

        # 3. Format target file content
        content_parts = []
        is_same_file = os.path.abspath(source_filepath) == os.path.abspath(target_filepath)
        is_new_file = not os.path.exists(target_filepath) or is_same_file

        if is_new_file and imports:
            content_parts.append("\n".join(imports))

        if wrap_in_class:
            class_body = "\n\n".join(extracted_blocks)
            content_parts.append(f"class {wrap_in_class}:\n{class_body}")
        else:
            content_parts.append("\n\n".join(extracted_blocks))

        final_code = "\n\n".join(content_parts) + "\n"

        # 4. Write safely (overwrite if target is source or new file)
        os.makedirs(os.path.dirname(target_filepath), exist_ok=True)
        write_mode = 'w' if is_same_file else ('a' if os.path.exists(target_filepath) else 'w')

        with open(target_filepath, write_mode, encoding='utf-8') as f:
            f.write(final_code)

        return f"Success: Extracted {found_blocks} blocks and written to {os.path.basename(target_filepath)}"

    except Exception as e:
        return f"Extraction Error: {e}"


def replace_lines(filepath: str, start_line: int, end_line: int, content: str,
                   expected_start_snippet: str = None, expected_end_snippet: str = None) -> str:
    path = Path(filepath)
    if not path.exists():
        return f"Error: File '{filepath}' does not exist."

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        return f"Error: Invalid line range [{start_line}, {end_line}] for file with {len(lines)} lines."

    def _find_unique_hint(snippet, anchor_label):
        matches = [i + 1 for i, line in enumerate(lines) if snippet in line.strip()]
        if len(matches) == 1:
            return matches[0], f"\nThat exact text was found at line {matches[0]} instead."
        elif len(matches) > 1:
            return None, (
                f"\nThat text appears at {len(matches)} different lines: {matches}. "
                f"Pick the correct one based on the surrounding function you intend to replace."
            )
        else:
            return None, (
                f"\nThat exact text was not found anywhere else in the file either. "
                f"Re-read the file with read_file and copy the {anchor_label} exactly "
                f"as it appears, including whitespace."
            )

    # --- START ANCHOR CHECK ---
    actual_start_line = lines[start_line - 1].strip()
    if expected_start_snippet and expected_start_snippet.strip() not in actual_start_line:
        snippet = expected_start_snippet.strip()
        found_line, hint = _find_unique_hint(snippet, "expected_start_snippet")
        if found_line is not None:
            offset = found_line - start_line
            corrected_end = end_line + offset
            hint += (
                f" Retry with start_line={found_line} and end_line={corrected_end} "
                f"(same range length, shifted by {offset:+d}). Keep expected_start_snippet "
                f"and expected_end_snippet the same as this attempt."
            )
        return (
            f"Error: Line {start_line} does not contain what you expected.\n"
            f"You expected something like: {snippet!r}\n"
            f"Line {start_line} actually contains: {actual_start_line!r}"
            f"{hint}"
        )

    # --- END ANCHOR CHECK ---
    # expected_end_snippet describes the line immediately AFTER the replaced range
    # (e.g. the next function's signature) -- NOT the last replaced line itself.
    if expected_end_snippet:
        snippet = expected_end_snippet.strip()
        # Allow up to a few blank/whitespace-only lines between end_line and the next
        # real content, so the model doesn't need to count trailing blank lines exactly.
        next_content_idx = None
        for i in range(end_line, min(end_line + 5, len(lines))):
            if lines[i].strip():
                next_content_idx = i
                break

        actual_next_line = lines[next_content_idx].strip() if next_content_idx is not None else ""
        if snippet not in actual_next_line:
            found_line, hint = _find_unique_hint(snippet, "expected_end_snippet")
            if found_line is not None:
                corrected_end = found_line - 1
                hint += (
                    f" Retry with end_line={corrected_end} (keep start_line={start_line} as-is). "
                    f"Keep expected_start_snippet and expected_end_snippet the same as this attempt."
                )
            return (
                f"Error: The line after end_line ({end_line}) does not contain what you expected.\n"
                f"You expected the NEXT block after your replacement to start with: {snippet!r}\n"
                f"The next non-blank line actually contains: {actual_next_line!r}\n"
                f"This usually means end_line is wrong -- expected_end_snippet should be the signature "
                f"of the NEXT function/block, not the last line inside the one you're replacing.{hint}"
            )

    old_slice = "".join(lines[start_line - 1:end_line])

    new_lines = [line + "\n" for line in content.splitlines()]
    lines[start_line - 1:end_line] = new_lines

    path.write_text("".join(lines), encoding="utf-8")

    return (
        f"Successfully replaced lines {start_line}-{end_line} in {filepath}.\n"
        f"--- Old content (for verification) ---\n{old_slice}"
        f"--- New content ---\n{content}\n"
    )


def patch_file(filepath: str, old_content: str, new_content: str) -> str:
    path = Path(filepath)
    if not path.exists():
        return f"Error: File '{filepath}' does not exist."
    if not old_content:
        return "Error: 'old_content' must not be empty. Provide the exact existing code to replace."

    # Read + normalize BEFORE any check that needs to inspect file content
    raw = path.read_text(encoding="utf-8")
    uses_crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    old = old_content.replace("\r\n", "\n")
    new = new_content.replace("\r\n", "\n")

    # --- STRICT CONTEXT VALIDATION ---
    if old_content.count('\n') < 2:
        line_count = old_content.count('\n') + 1
        idx = text.find(old.strip())
        context_hint = ""
        if idx != -1:
            start = text.rfind('\n', 0, idx)
            end = text.find('\n', idx + len(old))
            prev_line_start = text.rfind('\n', 0, start) if start != -1 else -1
            next_line_end = text.find('\n', end + 1) if end != -1 else -1
            if prev_line_start != -1 and next_line_end != -1:
                suggested = text[prev_line_start+1:next_line_end]
                context_hint = f"\nSuggested old_content:\n{suggested}"
        return (
            f"Error: 'old_content' has only {line_count} line(s); at least 3 lines are required."
            f"{context_hint}"
        )

    occurrences = text.count(old)

    if occurrences == 0:
        # Assuming _closest_match_hint is defined elsewhere in your toolset
        hint = _closest_match_hint(text, old)
        return (
            f"Error: 'old_content' was not found VERBATIM in '{filepath}' (0 exact matches).\n"
            f"This usually means the file has changed since you last read it, or the "
            f"whitespace/indentation doesn't match exactly.\n"
            f"Action: re-read the current file content and copy the exact text you want "
            f"to replace, including indentation.{hint}"
        )

    if occurrences > 1:
        return (
            f"Error: 'old_content' matched {occurrences} separate locations in "
            f"'{filepath}' — it must be unique.\n"
            f"Action: include more surrounding context (e.g. the function signature, "
            f"or a preceding/following line) so the match is unambiguous."
        )

    new_text = text.replace(old, new, 1)
    if uses_crlf:
        new_text = new_text.replace("\n", "\r\n")

    path.write_text(new_text, encoding="utf-8")

    old_line_count = old.count("\n") + 1
    new_line_count = new.count("\n") + 1
    delta = new_line_count - old_line_count
    delta_str = f"+{delta}" if delta > 0 else str(delta)

    size_warning = ""
    if old_line_count >= 5 and new_line_count <= 1:
        size_warning = (
            f"\nNote: this collapsed {old_line_count} lines down to {new_line_count}. "
            f"If you only meant to ADD something (like an import) rather than replace "
            f"an entire block, double-check this was intentional."
        )

    return (
        f"Successfully replaced {old_line_count} line(s) with {new_line_count} line(s) "
        f"in '{filepath}' (line delta: {delta_str}).{size_warning}"
    )


def _closest_match_hint(text: str, old: str, context_lines: int = 2) -> str:
    """
    Best-effort diagnostic for a failed match: scan for the closest whitespace-
    insensitive candidate of the same size
    """
    file_lines = text.split("\n")
    old_lines = old.split("\n")
    window = len(old_lines)

    if window == 0 or len(file_lines) < window or len(file_lines) > 5000:
        return ""  # skip on pathological input or very large files (cost control)

    normalized_old = "\n".join(l.strip() for l in old_lines)
    best_ratio, best_start = 0.0, None

    for i in range(len(file_lines) - window + 1):
        normalized_candidate = "\n".join(l.strip() for l in file_lines[i:i + window])
        ratio = difflib.SequenceMatcher(None, normalized_old, normalized_candidate).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i

    if best_start is not None and best_ratio > 0.6:
        start = max(0, best_start - context_lines)
        end = min(len(file_lines), best_start + window + context_lines)
        snippet = "\n".join(file_lines[start:end])
        return (
            f"\n\nClosest match found around line {best_start + 1} "
            f"(similarity: {best_ratio:.0%}):\n---\n{snippet}\n---"
        )
    return ""


# Common directories and file types to hide from the LLM to save context
IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', 'env', '.venv', 'build', 'dist', '.idea', '.vscode'}
IGNORE_EXTS = {'.pyc', '.exe', '.dll', '.so', '.dylib', '.png', '.jpg', '.jpeg', '.pdf', '.zip', '.tar', '.gz', '.mp4'}


def list_tree(dir_path=".", max_depth=2):
    """
    Returns a visual tree structure of the codebase.
    Crucial for allowing the agent to discover files on its own.
    """
    try:
        max_depth = int(max_depth)
        base_path = Path(dir_path).resolve()

        if not base_path.exists() or not base_path.is_dir():
            return f"Error: '{dir_path}' is not a valid directory."

        tree_str = []

        def walk(current_path, current_depth, prefix=""):
            if current_depth > max_depth:
                return
            try:
                # Filter out hidden files and ignored directories
                items = sorted([item for item in current_path.iterdir()
                                if item.name not in IGNORE_DIRS
                                and not item.name.startswith('.')])
            except PermissionError:
                return

            limit = 50  # Prevent context blowup in massive flat directories
            for i, item in enumerate(items[:limit]):
                is_last = (i == min(len(items), limit) - 1)
                connector = "└── " if is_last else "├── "
                tree_str.append(f"{prefix}{connector}{item.name}")

                if item.is_dir():
                    extension = "    " if is_last else "│   "
                    walk(item, current_depth + 1, prefix + extension)

            if len(items) > limit:
                tree_str.append(f"{prefix}└── ... [{len(items) - limit} more items hidden. Use a more specific path.]")

        tree_str.append(f"{base_path.name}/")
        walk(base_path, 1)

        result = "\n".join(tree_str)
        return result if result.strip() else f"Directory '{dir_path}' is empty."

    except Exception as e:
        return f"Error generating tree: {e}"


def search_codebase(dir_path=".", query="", is_regex=False, max_matches=50):
    """
    Native Python grep equivalent. Searches for a string or regex pattern
    across all text files in the directory.
    """
    try:
        max_matches = int(max_matches)
        base_path = Path(dir_path).resolve()

        if not base_path.exists() or not base_path.is_dir():
            return f"Error: '{dir_path}' is not a valid directory."

        if not query:
            return "Error: Search query cannot be empty."

        if not str(is_regex).lower() in ['true', '1', 't']:
            query = re.escape(query)

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return f"Error: Invalid regex pattern - {e}"

        results = []
        match_count = 0

        for root, dirs, files in os.walk(base_path):
            # Filter directories in-place to avoid traversing into ignored paths
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]

            for file in files:
                if any(file.endswith(ext) for ext in IGNORE_EXTS) or file.startswith('.'):
                    continue

                filepath = Path(root) / file
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        for line_num, line in enumerate(f, 1):
                            if pattern.search(line):
                                # Make paths relative to keep output clean and short
                                rel_path = filepath.relative_to(base_path)
                                # Strip whitespace and truncate very long minified lines
                                clean_line = line.strip()[:120]
                                results.append(f"{rel_path}:{line_num}: {clean_line}")
                                match_count += 1

                                if match_count >= max_matches:
                                    results.append(
                                        f"\n... [TRUNCATED] Reached maximum of {max_matches} matches. Please narrow your search query.")
                                    return "\n".join(results)
                except (UnicodeDecodeError, PermissionError):
                    # Silently skip binary files or files without read permissions
                    continue

        if not results:
            return f"No matches found for '{query}' in {dir_path}."

        return "\n".join(results)

    except Exception as e:
        return f"Error searching codebase: {e}"
