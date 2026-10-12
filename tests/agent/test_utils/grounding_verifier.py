import os
import re
import ast


class GroundingVerifier:
    """Extracts valid symbols, directories, and dependencies from target repo and verifies README claims against them."""

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
        # 1. Walk repository to register files, modules, classes, functions, and directories
        for root, dirs, files in os.walk(self.repo_dir):
            rel_root = os.path.relpath(root, self.repo_dir).replace("\\", "/")

            if rel_root != ".":
                self.valid_symbols.add(rel_root)
                self.valid_symbols.add(rel_root + "/")
                for part in rel_root.split("/"):
                    self.valid_symbols.add(part)

            for d in dirs:
                self.valid_symbols.add(d)
                d_rel = os.path.join(rel_root, d).replace("\\", "/") if rel_root != "." else d
                self.valid_symbols.add(d_rel)
                self.valid_symbols.add(d_rel + "/")

            for f in files:
                rel_path = os.path.join(rel_root, f).replace("\\", "/") if rel_root != "." else f
                self.valid_symbols.add(f)
                self.valid_symbols.add(rel_path)

                # If it's a Python file, register module name and AST definitions
                if f.endswith(".py"):
                    self.valid_symbols.add(f[:-3])

                    full_p = os.path.join(root, f)
                    try:
                        with open(full_p, "r", encoding="utf-8") as py_f:
                            code = py_f.read()

                            # AST Parsing for symbols and flags
                            tree = ast.parse(code, filename=f)
                            for node in ast.walk(tree):
                                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                                    self.valid_symbols.add(node.name)

                                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                                    if node.value.startswith('-') or node.value.startswith('/'):
                                        self.valid_flags.add(node.value.strip())

                                elif getattr(ast, 'Str', None) and isinstance(node, getattr(ast, 'Str')):
                                    if node.s.startswith('-') or node.s.startswith('/'):
                                        self.valid_flags.add(node.s.strip())

                            # Aggressive Regex Fallback for flags/commands
                            flags = re.findall(r'["\']\s*(-[a-zA-Z0-9\-_]+|--[a-zA-Z0-9\-_]+|/[a-zA-Z0-9\-_]+)\s*["\']',
                                               code)
                            self.valid_flags.update(flags)
                    except Exception:
                        pass

        # 2. Parse project metadata/dependency files to whitelist real external packages
        for req_file in ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"):
            req_path = os.path.join(self.repo_dir, req_file)
            if os.path.isfile(req_path):
                try:
                    with open(req_path, "r", encoding="utf-8", errors="ignore") as rf:
                        content = rf.read()
                        if "requirements" in req_file or "setup" in req_file:
                            for line in content.splitlines():
                                line = line.strip()
                                if line and not line.startswith("#"):
                                    pkg = re.split(r'==|>=|<=| @ |\[', line)[0].strip()
                                    if pkg:
                                        self.valid_symbols.add(pkg)
                        elif "toml" in req_file:
                            matches = re.findall(r'["\']([a-zA-Z0-9\-_]+)(?:==|>=|<=)?', content)
                            for m in matches:
                                self.valid_symbols.add(m)
                except Exception:
                    pass

    def evaluate_readme(self, readme_content: str):
        # 1. Extract triple-backtick code blocks first
        blocks = re.findall(r'```[a-zA-Z]*\n(.*?)```', readme_content, re.DOTALL)

        # 2. Remove those blocks from text to prevent regex crossover
        clean_text = re.sub(r'```.*?```', '', readme_content, flags=re.DOTALL)

        # 3. Extract single inline backticks
        inlines = re.findall(r'`([^`\n]+)`', clean_text)

        all_code_string = " ".join(blocks + inlines)
        raw_tokens = all_code_string.split()

        valid_count = 0
        hallucinations = []
        total_tokens = 0

        ignore_words = {
            "python", "python3", "pip", "install", "run", "usage", "bash", "sh",
            "cd", "git", "clone", "the", "a", "to", "and", "is", "for", "with",
            "in", "of", "this", "file", "script", "use", "true", "false", "none",
            "checkout", "commit", "push", "pull", "add", "status", "branch", "merge",
            "origin", "main", "master", "feature", "bugfix", "your", "repo", "repository",
            "fork", "ctrl+c", "ctrl", "alt", "shift", "enter"
        }

        standard_cli_flags = {
            "-r", "-m", "-v", "-h", "--help", "--user",
            "--upgrade", "--no-cache-dir", "-b"
        }

        valid_symbols_lower = {s.lower() for s in self.valid_symbols if isinstance(s, str)}

        for token in raw_tokens:
            token_clean = token.strip('.,;:\'"()[]{}')
            token_clean_slash = token_clean.rstrip('/')
            token_lower = token_clean.lower()
            token_lower_slash = token_clean_slash.lower()

            if token_lower in ignore_words or token_lower in standard_cli_flags:
                continue

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

            if (token_clean in self.valid_symbols or
                    token_clean_slash in self.valid_symbols or
                    token_lower in valid_symbols_lower or
                    token_lower_slash in valid_symbols_lower or
                    token_clean in self.valid_flags or
                    token_clean_slash in self.valid_flags):
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
