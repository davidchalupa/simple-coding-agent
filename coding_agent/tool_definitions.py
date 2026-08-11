import os
import subprocess
import ast
import re
from pathlib import Path


# 2. Tool Definitions
def read_file(filepath, start_line=1, max_lines=100):
    """Reads a file with strict pagination to prevent context window exhaustion."""
    try:
        if not os.path.isfile(filepath):
            return f"Error: '{filepath}' is not a file."

        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        total_lines = len(lines)
        start_idx = max(0, int(start_line) - 1)
        end_idx = start_idx + max(1, int(max_lines))

        selected_lines = lines[start_idx:end_idx]
        content = "".join(selected_lines)

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


def patch_file(filepath, search_text, replace_text):
    """Surgically replaces a specific block of text inside a file."""
    try:
        if not os.path.exists(filepath):
            return f"Error: File '{filepath}' does not exist."

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if search_text not in content:
            return "Error: The exact 'search_text' block was not found in the file. Patch failed."

        updated_content = content.replace(search_text, replace_text, 1)  # Only replace first match for safety

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        return f"Successfully patched {filepath}."
    except Exception as e:
        return f"Error patching file: {e}"


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

    try:
        with open(source_filepath, 'r', encoding='utf-8') as f:
            source = f.read()

        tree = ast.parse(source)
        extracted_code = []

        # 1. Grab all global imports from the original file
        imports = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                segment = ast.get_source_segment(source, node)
                if segment:
                    imports.append(segment)

        # 2. Extract the requested blocks
        if wrap_in_class:
            extracted_code.append(f"class {wrap_in_class}:")
            indent_prefix = "    "
        else:
            indent_prefix = ""

        found_blocks = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in block_names:
                    segment = ast.get_source_segment(source, node)
                    if segment:
                        if wrap_in_class and not isinstance(node, ast.ClassDef):
                            segment = "\n".join(indent_prefix + line if line.strip() else line
                                                for line in segment.splitlines())
                        extracted_code.append(segment)
                        found_blocks += 1

        if found_blocks == 0:
            return f"Error: None of the requested blocks ({block_names}) were found."

        # 3. Write to the new file safely
        is_new_file = not os.path.exists(target_filepath)
        os.makedirs(os.path.dirname(target_filepath), exist_ok=True)

        with open(target_filepath, 'a', encoding='utf-8') as f:
            # If creating the file for the first time, inject the original imports at the top
            if is_new_file and imports:
                f.write("\n".join(imports) + "\n\n")

            f.write("\n\n".join(extracted_code) + "\n\n")

        return f"Success: Extracted {found_blocks} blocks and appended to {os.path.basename(target_filepath)}"

    except Exception as e:
        return f"Extraction Error: {e}"


def replace_lines(filepath: str, start_line: int, end_line: int, content: str) -> str:
    """Replaces a range of lines (inclusive, 1-indexed) with content."""
    path = Path(filepath)
    if not path.exists():
        return f"Error: File '{filepath}' does not exist."

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        return f"Error: Invalid line range [{start_line}, {end_line}] for file with {len(lines)} lines."

    # Convert 1-based indices to 0-based slice
    new_lines = [line + "\n" if not line.endswith("\n") else line for line in content.splitlines()]
    lines[start_line - 1:end_line] = new_lines

    path.write_text("".join(lines), encoding="utf-8")
    return f"Successfully replaced lines {start_line}-{end_line} in {filepath}."


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
