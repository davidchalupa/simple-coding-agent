import os
import re
import ast


class GroundingVerifier:
    """Extracts valid symbols from target repo and verifies README claims against them."""

    def __init__(self, repo_dir: str):
        self.repo_dir = os.path.abspath(repo_dir)
        self.valid_symbols = set()
        self.valid_flags = set()

        # Trust the root repository name
        repo_basename = os.path.basename(self.repo_dir)
        self.valid_symbols.add(repo_basename)
        self.valid_symbols.add(f"{repo_basename}.git")

        self._build_symbol_manifest()

    def _build_symbol_manifest(self):
        for root, _, files in os.walk(self.repo_dir):
            for f in files:
                rel_path = os.path.relpath(os.path.join(root, f), self.repo_dir).replace("\\", "/")

                # Add exact filename and relative path
                self.valid_symbols.add(f)
                self.valid_symbols.add(rel_path)

                # If it's a Python file, also register the module name (without .py)
                if f.endswith(".py"):
                    self.valid_symbols.add(f[:-3])

                    full_p = os.path.join(root, f)
                    try:
                        with open(full_p, "r", encoding="utf-8") as py_f:
                            code = py_f.read()

                            # 1. AST Parsing for guaranteed extraction
                            tree = ast.parse(code, filename=f)
                            for node in ast.walk(tree):
                                # Extract Classes and Functions (including async)
                                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                                    self.valid_symbols.add(node.name)

                                # Extract String Literals (Python 3.8+)
                                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                                    if node.value.startswith('-') or node.value.startswith('/'):
                                        self.valid_flags.add(node.value.strip())

                                # Extract String Literals (Python < 3.8 fallback)
                                elif getattr(ast, 'Str', None) and isinstance(node, getattr(ast, 'Str')):
                                    if node.s.startswith('-') or node.s.startswith('/'):
                                        self.valid_flags.add(node.s.strip())

                            # 2. Aggressive Regex Fallback (for non-standard formatting)
                            flags = re.findall(r'["\']\s*(-[a-zA-Z0-9\-_]+|--[a-zA-Z0-9\-_]+|/[a-zA-Z0-9\-_]+)\s*["\']',
                                               code)
                            self.valid_flags.update(flags)
                    except Exception:
                        pass

    def evaluate_readme(self, readme_content: str):
        # 1. Extract triple-backtick code blocks first
        blocks = re.findall(r'```[a-zA-Z]*\n(.*?)```', readme_content, re.DOTALL)

        # 2. Remove those blocks from the text to prevent regex crossover
        clean_text = re.sub(r'```.*?```', '', readme_content, flags=re.DOTALL)

        # 3. Now safely extract single inline backticks
        inlines = re.findall(r'`([^`\n]+)`', clean_text)

        # Combine all extracted code and split it into individual words/tokens
        all_code_string = " ".join(blocks + inlines)
        raw_tokens = all_code_string.split()

        valid_count = 0
        hallucinations = []
        total_tokens = 0

        # Ignore standard English, bash syntax, and common Git/contribution boilerplate
        ignore_words = {
            "python", "python3", "pip", "install", "run", "usage", "bash", "sh",
            "cd", "git", "clone", "the", "a", "to", "and", "is", "for", "with",
            "in", "of", "this", "file", "script", "use", "true", "false", "none",
            # Git & Boilerplate additions
            "checkout", "commit", "push", "pull", "add", "status", "branch", "merge",
            "origin", "main", "master", "feature", "bugfix", "your", "repo", "repository",
            "fork", "ctrl+c", "ctrl", "alt", "shift", "enter"
        }

        standard_cli_flags = {
            "-r", "-m", "-v", "-h", "--help", "--user",
            "--upgrade", "--no-cache-dir", "-b"
        }

        # Create a lowercase map of valid symbols for case-insensitive fallback matching
        valid_symbols_lower = {s.lower() for s in self.valid_symbols if isinstance(s, str)}

        for token in raw_tokens:
            # Strip surrounding punctuation
            token_clean = token.strip('.,;:\'"()[]{}')
            token_lower = token_clean.lower()

            # Skip explicitly ignored terms and standard flags
            if token_lower in ignore_words or token_lower in standard_cli_flags:
                continue

            # Skip placeholders and URLs
            if (token_lower.startswith('http://') or
                    token_lower.startswith('https://') or
                    'github.com' in token_lower or
                    'path/to' in token_lower or
                    'your-repo' in token_lower or
                    token_lower.startswith('feature/')):
                continue

            is_cli_flag = token_clean.startswith('-') and len(token_clean) >= 2
            is_macro = token_clean.startswith('/') and len(token_clean) >= 2

            if not token_clean:
                continue

            if len(token_clean) <= 2 and not (is_cli_flag or is_macro):
                continue

            total_tokens += 1

            # Check exact match first, then case-insensitive, then flags
            if (token_clean in self.valid_symbols or
                    token_lower in valid_symbols_lower or
                    token_clean in self.valid_flags):
                valid_count += 1
            else:
                hallucinations.append(token_clean)

        total = total_tokens
        groundedness = (valid_count / total) * 100 if total > 0 else 0
        word_count = len(readme_content.split())
        density = (valid_count / word_count * 1000) if word_count > 0 else 0

        return {
            "groundedness_score": round(groundedness, 2),
            "density_score": round(density, 2),
            "total_code_references": total,
            "hallucination_candidates": list(set(hallucinations))
        }
