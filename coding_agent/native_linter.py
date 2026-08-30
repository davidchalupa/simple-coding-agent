import os
import ast
import builtins
import re


def attempt_quote_delimiter_fix(filepath, error_line_no):
    """
    Narrow, deterministic repair for a specific recurring failure: a line uses
    single-quote string delimiters but the string's own content contains an
    unescaped literal single quote (e.g. print('Use 'c' to click')). If the
    line contains no double quotes at all, it's almost always safe to swap
    the outer delimiters to double quotes instead of trying to escape the
    inner ones.

    Returns True if a fix was applied and the file was rewritten, else False.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        if error_line_no < 1 or error_line_no > len(lines):
            return False

        idx = error_line_no - 1
        line = lines[idx]

        # Bail out if the line contains any double quote already -- ambiguous,
        # don't guess.
        if '"' in line:
            return False

        # Heuristic: only proceed if there are enough single-quote characters
        # to indicate a genuinely broken multi-quote string, not ordinary code.
        if line.count("'") < 4:
            return False

        first = line.find("'")
        last = line.rfind("'")
        if first == -1 or first == last:
            return False

        inner = line[first + 1:last]
        # Un-escape any already-escaped single quotes the model may have
        # inserted, since they're no longer needed once we swap to double
        # quotes as the delimiter.
        inner = inner.replace("\\'", "'")

        new_line = line[:first] + '"' + inner + '"' + line[last + 1:]

        candidate_lines = lines[:]
        candidate_lines[idx] = new_line
        candidate_source = ''.join(candidate_lines)

        try:
            ast.parse(candidate_source)
        except SyntaxError:
            return False  # Fix didn't actually resolve it; don't apply.

        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(candidate_lines)

        return True

    except Exception:
        return False


class DependencyChecker(ast.NodeVisitor):
    def __init__(self):
        self.missing_names = set()
        self.global_names = set(dir(builtins))
        self.scopes = [self.global_names]  # Stack of scope sets. Index 0 is global.

    def visit_Module(self, node):
        # PASS 1: Gather all global definitions (including those inside if/try/with blocks)
        class GlobalVisitor(ast.NodeVisitor):
            def __init__(self, global_names):
                self.global_names = global_names

            def visit_Name(self, n):
                if isinstance(n.ctx, ast.Store):
                    self.global_names.add(n.id)

            def visit_FunctionDef(self, n):
                self.global_names.add(n.name)
                # Stop traversal: Do not bleed into function body

            def visit_AsyncFunctionDef(self, n):
                self.global_names.add(n.name)
                # Stop traversal

            def visit_ClassDef(self, n):
                self.global_names.add(n.name)
                # Stop traversal

            def visit_Import(self, n):
                for alias in n.names:
                    name = alias.asname or alias.name
                    self.global_names.add(name.split('.')[0])

            def visit_ImportFrom(self, n):
                for alias in n.names:
                    name = alias.asname or alias.name
                    self.global_names.add(name)

        GlobalVisitor(self.global_names).visit(node)

        # PASS 2: Traverse code to check for scope violations and usage
        self.generic_visit(node)

    def _add_to_scope(self, node, scope_set):
        """Recursively extract variable names from assignments."""
        if isinstance(node, ast.Name):
            scope_set.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self._add_to_scope(elt, scope_set)

    def _get_locals(self, node):
        """Finds all locally assigned variables in a block to prevent false forward-reference errors."""
        local_vars = set()

        class LocalVisitor(ast.NodeVisitor):
            def visit_Name(self, n):
                if isinstance(n.ctx, ast.Store):
                    local_vars.add(n.id)

            def visit_arg(self, n):
                local_vars.add(n.arg)

            def visit_ExceptHandler(self, n):
                if n.name:
                    local_vars.add(n.name)
                self.generic_visit(n)

            def visit_FunctionDef(self, n):
                pass  # Do not bleed into nested functions

            def visit_ClassDef(self, n):
                pass  # Do not bleed into nested classes

        for stmt in getattr(node, 'body', []):
            LocalVisitor().visit(stmt)
        return local_vars

    def visit_FunctionDef(self, node):
        # 1. Get locally assigned variables
        local_vars = self._get_locals(node)

        # 2. Add function arguments to local scope
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            local_vars.add(arg.arg)
        if node.args.vararg:
            local_vars.add(node.args.vararg.arg)
        if node.args.kwarg:
            local_vars.add(node.args.kwarg.arg)

        # 3. Push new scope onto the stack
        self.scopes.append(local_vars)

        # 4. Visit function body
        for stmt in node.body:
            self.visit(stmt)

        # 5. Pop scope after exiting function
        self.scopes.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        self.scopes.append(self._get_locals(node))
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            # Check if variable is available in any accessible scope (Local -> Enclosing -> Global)
            found = False
            for scope in reversed(self.scopes):
                if node.id in scope:
                    found = True
                    break
            if not found:
                self.missing_names.add(node.id)
        self.generic_visit(node)


def find_symbol_definitions(workspace_dir, missing_names):
    """Scans sibling Python files to find where missing names are defined."""
    found_locations = {name: [] for name in missing_names}
    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('venv', '__pycache__', 'env')]
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        source = f.read()
                    tree = ast.parse(source, filename=file_path)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            if node.name in missing_names:
                                found_locations[node.name].append(os.path.relpath(file_path, workspace_dir))
                        elif isinstance(node, ast.Assign):
                            for target in node.targets:
                                if isinstance(target, ast.Name) and target.id in missing_names:
                                    found_locations[target.id].append(os.path.relpath(file_path, workspace_dir))
                except Exception:
                    pass
    return found_locations


def check_python_syntax_and_imports(filepath, workspace_dir=None):
    """Checks Python files for syntax errors AND missing imports/variables."""
    if not filepath.endswith('.py'):
        return None

    if workspace_dir is None:
        workspace_dir = os.path.dirname(os.path.abspath(filepath))

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()

        tree = ast.parse(source, filename=filepath)

        checker = DependencyChecker()
        checker.visit(tree)

        missing_names = checker.missing_names

        if missing_names:
            error_msg = f"Linter Error: The following names/modules are used but never imported or defined: {list(missing_names)}\n"

            locations = find_symbol_definitions(workspace_dir, missing_names)
            hints = []

            for name, paths in locations.items():
                if paths:
                    module_names = [p.replace('.py', '').replace(os.sep, '.') for p in paths]
                    hints.append(f"  - '{name}' is defined in: {', '.join(paths)} (import via `{module_names[0]}`)")

            if hints:
                error_msg += "\n[Workspace Hints - Do not guess, use these]:\n" + "\n".join(hints)
            else:
                error_msg += "\n[Workspace Hints]: Could not find definitions for these symbols in the current directory. You may need to create them, define them globally, or check standard library imports."

            return error_msg

        return None
    except SyntaxError as e:
        return f"SyntaxError on line {e.lineno}: {e.msg}\n{e.text}"
    except Exception as e:
        return f"Linter error: {e}"
