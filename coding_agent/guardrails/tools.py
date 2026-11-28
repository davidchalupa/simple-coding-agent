import os
import json
import re
import ast
import sys
import importlib.util


from coding_agent import split_tools
from coding_agent import native_linter


def verify_sandbox_health(split_file, sandbox_dir, messages):
    """Checks structural integrity and lints sandbox files."""
    print("\n⚙️  [System Guardrail] Analyzing sandbox refactoring health...")

    expected_files = []
    for msg in reversed(messages):
        content = msg.get("content", "")

        # 1. Try to find the XML tag first
        match = re.search(r"<blueprint>\s*(.*?)\s*</blueprint>", content, re.DOTALL)

        # 2. Fallback: If agent stubbornly used Markdown headers instead
        if not match:
            match = re.search(r"(?:###)?\s*BLUEPRINT\s*.*?```(?:json)?\s*\n(.*?)\n\s*```", content,
                              re.DOTALL | re.IGNORECASE)

        if match:
            try:
                # Clean up any residual markdown if it was wrapped inside the tags
                clean_json = re.sub(r'```json\s*|\s*```', '', match.group(1)).strip()
                plan = json.loads(clean_json)
                expected_files = list(plan.keys())
            except json.JSONDecodeError:
                pass
            break

    # Pass expected_files into the verifier
    passed, report = split_tools.verify_refactor_integrity(split_file, sandbox_dir, expected_files)

    if passed:
        for root, _, files in os.walk(sandbox_dir):
            for file in files:
                if file.endswith('.py') and not file.startswith('.'):
                    err = native_linter.check_python_syntax_and_imports(os.path.join(root, file))
                    if err: return False, f"Dependency Error in '{file}':\n{err}\nUse tools to add missing imports."
    return passed, report


def run_self_verification(filepath):
    """
    Generalized post-write self-verification for Python files.
    """
    if not filepath or not filepath.endswith(".py"):
        return None
    if not os.path.isfile(filepath):
        return None

    try:
        return native_linter.check_python_syntax_and_imports(filepath)
    except Exception as e:
        # Failsafe: never let a linter crash take down the agent loop.
        print(f"⚠️ [Self-Verification] Linter itself raised an error, skipping check: {e}")
        return None


def check_import_resolution(filepath, session_cwd):
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    search_dir = os.path.dirname(os.path.abspath(filepath))
    session_cwd = os.path.abspath(session_cwd)
    stdlib_names = getattr(sys, "stdlib_module_names", set())

    # Pragmatic allow-list for heavyweights commonly used in agent sandboxes
    # to bypass the checks immediately.
    KNOWN_THIRDPARTY = {
        "PyQt5", "PyQt6", "PySide2", "PySide6", "requests", "numpy", "pandas",
        "pytest", "matplotlib", "scipy", "flask", "django", "fastapi", "sqlalchemy",
        "boto3", "bs4", "pydantic", "yaml", "cv2", "PIL"
    }

    suspicious_imports = []

    def is_resolvable(top_level):
        if top_level in stdlib_names or top_level in KNOWN_THIRDPARTY:
            return True

        try:
            if importlib.util.find_spec(top_level) is not None:
                return True
        except (ImportError, ValueError, AttributeError):
            pass

        # Check Local Workspace Resolution
        for base_path in [search_dir, session_cwd]:
            if os.path.isfile(os.path.join(base_path, f"{top_level}.py")):
                return True
            if os.path.isdir(os.path.join(base_path, top_level)) and \
                    os.path.isfile(os.path.join(base_path, top_level, "__init__.py")):
                return True

        return False

    for node in ast.walk(tree):
        modules_to_check = []

        if isinstance(node, ast.ImportFrom) and node.module:
            modules_to_check.append((node.module, node.names))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules_to_check.append((alias.name, [alias]))

        for module_name, names_list in modules_to_check:
            top_level = module_name.split(".")[0]

            if not is_resolvable(top_level):
                # Don't fail immediately! Mark it as suspicious so we can cross-reference it.
                imported_symbols = [alias.asname or alias.name for alias in names_list]
                suspicious_imports.append((module_name, imported_symbols))

    if not suspicious_imports:
        return []

    # Gather all unresolved symbols (e.g., 'QApplication', 'my_custom_func')
    all_unresolved_symbols = set()
    for _, symbols in suspicious_imports:
        all_unresolved_symbols.update(symbols)

    # Cross-reference with the local workspace
    locations = {}
    if all_unresolved_symbols:
        # Search the whole session_cwd so we can provide accurate hints across the codebase
        locations = native_linter.find_symbol_definitions(session_cwd, all_unresolved_symbols)

    errors = []
    for module_name, symbols in suspicious_imports:
        local_hints = {}
        for sym in symbols:
            if sym in locations and locations[sym]:
                local_hints[sym] = locations[sym]

        # If we found ANY of the imported symbols elsewhere in the local repo,
        # we know for a fact the agent hallucinated the local import path.
        if local_hints:
            error_msg = [f"Unresolved import '{module_name}' — but the symbols exist elsewhere in your workspace."]
            hints = []
            for sym, paths in local_hints.items():
                module_names = [p.replace('.py', '').replace(os.sep, '.') for p in paths]
                hints.append(f"  - '{sym}' is defined in: {', '.join(paths)} (import via `{module_names[0]}`)")

            error_msg.append("[Workspace Hints - Do not guess, use these]:\n" + "\n".join(hints))
            errors.append("\n".join(error_msg))
        else:
            # The module is unresolvable AND its symbols do not exist in the repo.
            # In a sandbox, this is almost certainly an uninstalled 3rd-party package.
            # We safely suppress the static error here and let normal runtime execution
            # (which handles standard ModuleNotFoundErrors) take over if it's truly broken.
            pass

    return errors


def _stmt_list_falls_through(stmts):
    """True if control can reach past the end of this statement list without
    hitting a guaranteed return/raise."""
    for stmt in stmts:
        if _stmt_terminates(stmt):
            return False
    return True


def _stmt_terminates(stmt):
    """True if this statement guarantees control never proceeds to whatever follows it."""
    if isinstance(stmt, (ast.Return, ast.Raise)):
        return True
    if isinstance(stmt, ast.If):
        if not stmt.orelse:
            return False
        return (not _stmt_list_falls_through(stmt.body)
                and not _stmt_list_falls_through(stmt.orelse))
    if isinstance(stmt, ast.Try):
        body_ok = not _stmt_list_falls_through(stmt.body)
        handlers_ok = all(not _stmt_list_falls_through(h.body) for h in stmt.handlers) if stmt.handlers else body_ok
        if stmt.finalbody and not _stmt_list_falls_through(stmt.finalbody):
            return True
        return body_ok and handlers_ok
    if isinstance(stmt, ast.With):
        return not _stmt_list_falls_through(stmt.body)
    if isinstance(stmt, ast.While):
        is_infinite = isinstance(stmt.test, ast.Constant) and stmt.test.value is True
        if is_infinite and not _contains_break(stmt.body):
            return True  # loop only exits via return/raise inside it
        return False  # exits via condition-false or break -> falls through after loop
    return False  # For-loops, match, etc: conservatively assume they can fall through


def _contains_break(stmts):
    for stmt in stmts:
        if isinstance(stmt, ast.Break):
            return True
        if isinstance(stmt, (ast.While, ast.For)):
            continue  # a break here belongs to the nested loop, not this one
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.stmt) and _contains_break([child]):
                return True
    return False


def check_return_consistency(filepath, line_range=None):
    """
    Returns a list of human-readable warnings for functions that mix explicit
    non-None return values with a path that falls through to an implicit
    `return None` (or a bare `return`). This is the class of bug where an
    edit adds `return True`/`return False` to some branches but misses one.

    line_range: optional (start, end) to only check functions overlapping
    an edit, e.g. from a replace_lines/patch_file call. Pass None to check
    the whole file (use for write_file/append_file).
    """
    try:
        tree = ast.parse(open(filepath, "r", encoding="utf-8").read())
    except Exception:
        return []  # let run_self_verification report syntax errors; don't duplicate

    warnings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if line_range and not (node.lineno <= line_range[1] and node.end_lineno >= line_range[0]):
            continue

        returns = [n for n in ast.walk(node)
                   if isinstance(n, ast.Return)
                   and not any(n in ast.walk(inner) for inner in ast.walk(node)
                               if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not node)]
        has_explicit_value = any(r.value is not None and not
                                  (isinstance(r.value, ast.Constant) and r.value.value is None)
                                  for r in returns)
        has_bare_or_none = any(r.value is None or
                                (isinstance(r.value, ast.Constant) and r.value.value is None)
                                for r in returns)
        falls_through = _stmt_list_falls_through(node.body)

        if has_explicit_value and (has_bare_or_none or falls_through):
            reason = "a bare `return`/`return None`" if has_bare_or_none else "falling off the end of the function"
            warnings.append(
                f"Function '{node.name}' (line {node.lineno}) returns an explicit value on some "
                f"paths but has another path that ends via {reason}, which implicitly returns None. "
                f"If every path is meant to return the same type, add an explicit return on that path."
            )
    return warnings


def check_callback_arity(test_filepath, session_cwd):
    """
    Generic check: for every function call in test_filepath, if an argument passed is a lambda
    or a locally-defined function, and that same parameter position (by name, via the callee's
    own signature) is invoked as a call somewhere in the codebase, verify the lambda's arity
    matches how it's actually called.
    """
    import ast
    import os

    search_dir = os.path.dirname(os.path.abspath(test_filepath))
    test_filename = os.path.basename(test_filepath)

    func_params = {}  # func_name -> [param_names]
    call_arities = {}  # (func_name, param_name) -> set(observed call arities)

    # 1. Parse the codebase to find signatures and internal callback invocations
    for fname in os.listdir(search_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(search_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fpath)
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                param_names = [a.arg for a in node.args.args]
                func_params[node.name] = param_names

                # look for calls to any of this function's OWN parameters inside its body
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                            and inner.func.id in param_names):
                        arity = len(inner.args) + len(inner.keywords)
                        key = (node.name, inner.func.id)
                        if key not in call_arities:
                            call_arities[key] = set()
                        call_arities[key].add(arity)

    if not call_arities:
        return []

    # 2. Parse the test file to find lambdas being passed as those callbacks
    with open(test_filepath, "r", encoding="utf-8") as f:
        test_tree = ast.parse(f.read(), filename=test_filepath)

    errors = []

    # Helper for generating a heavily prescriptive, LLM-optimized error message
    def build_error_msg(lineno, pname, actual_arity, expected_arities, func_name):
        expected_str = " or ".join(map(str, expected_arities))
        # Create a dummy signature for the largest expected arity to make it foolproof
        max_expected = max(expected_arities)
        dummy_args = ", ".join([f"arg{i + 1}" for i in range(max_expected)])

        return (
            f"\n[ARITY MISMATCH] Line {lineno} in `{test_filename}`:\n"
            f"The lambda passed for '{pname}' accepts {actual_arity} argument(s), but `{func_name}` invokes it with {expected_str} argument(s).\n\n"
            f"ACTION REQUIRED TO FIX THIS TEST:\n"
            f"Option 1 (Highly Recommended - Prevents infinite loops):\n"
            f"  Replace the lambda entirely with a mock object:\n"
            f"  `from unittest.mock import MagicMock`\n"
            f"  `mock_{pname} = MagicMock(side_effect=[...])`\n\n"
            f"Option 2 (Fix the Lambda Signature):\n"
            f"  Update your lambda to accept exact arguments: `lambda {dummy_args}: ...`\n"
            f"  Or use catch-all arguments: `lambda *args, **kwargs: ...`\n\n"
            f"DO NOT attempt to modify `{func_name}`'s source code."
        )

    # Helper to check if a lambda's arguments match expectations
    def check_lambda_node(lam_node, pname, func_name):
        key = (func_name, pname)
        if key not in call_arities:
            return None

        expected_arities = call_arities[key]

        # Safely calculate total explicit arguments (handling Python 3.8+ posonlyargs)
        posonly = len(getattr(lam_node.args, 'posonlyargs', []))
        args_len = len(lam_node.args.args)
        kwonly = len(lam_node.args.kwonlyargs)
        actual_arity = posonly + args_len + kwonly

        has_varargs = lam_node.args.vararg is not None
        has_kwargs = lam_node.args.kwarg is not None

        # If it has *args or **kwargs, it's safe. Otherwise, check exact match.
        if not (has_varargs or has_kwargs) and actual_arity not in expected_arities:
            return build_error_msg(lam_node.lineno, pname, actual_arity, expected_arities, func_name)
        return None

    for node in ast.walk(test_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            func_name = node.func.id
            callee_params = func_params.get(func_name)
            if not callee_params:
                continue

            # Check positional arguments
            for i, arg in enumerate(node.args):
                if isinstance(arg, ast.Lambda) and i < len(callee_params):
                    pname = callee_params[i]
                    err = check_lambda_node(arg, pname, func_name)
                    if err: errors.append(err)

            # Check keyword arguments
            for kw in node.keywords:
                if isinstance(kw.value, ast.Lambda):
                    pname = kw.arg
                    err = check_lambda_node(kw.value, pname, func_name)
                    if err: errors.append(err)

    return errors


def check_constant_closures(test_filepath):
    import ast
    with open(test_filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=test_filepath)

    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda):
            param_names = {a.arg for a in node.args.args}
            if node.args.vararg:
                param_names.add(node.args.vararg.arg)
            if not param_names:
                continue
            used_names = {n.id for n in ast.walk(node.body) if isinstance(n, ast.Name)}
            if not (param_names & used_names):
                errors.append(
                    f"Line {node.lineno}: lambda ignores all its arguments and will return "
                    f"the same result every time it's called — this can cause an infinite "
                    f"loop if used as a repeated callback (e.g. get_action in a game loop)."
                )
    return errors


def check_test_coverage_regression(filepath):
    import ast
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    test_methods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                    test_methods.append(item.name)

    if not test_methods:
        return [f"{os.path.basename(filepath)} contains ZERO test methods. If you were only "
                f"fixing an unrelated error (e.g. an import), you must preserve the existing "
                f"test methods — do not delete test content while fixing something else."]
    return []


NO_APPLY_REQUESTED_PATTERNS = re.compile(
    r"\b("
    # Catch "just / only write / show / draft / output"
    r"(just|only)\s+(show|output|print|display|write(\s+down)?|provide|draft|plan)|"
    # Catch "don't / do not use / call / write / apply / make changes"
    r"(do\s+not|don'?t)\s+(use|call|write|apply|save|modify|edit|change|make\s+(any\s+)?(modifications|changes|edits))|"
    # Catch "without applying / modifying / using"
    r"without\s+(using|calling|writing|applying|saving|modifying|editing|making\s+changes)|"
    # Catch explicit markdown/code requests (allowing optional words like "standard")
    r"in\s+(an?|the)\s+([a-zA-Z]+\s+)*(markdown|code)\s+block|"
    r"(show|give)\s+(me\s+)?the code|"
    r"no\s+need\s+to\s+(use|call|write|apply|save|modify|edit)"
    r")\b",
    re.IGNORECASE,
)

def looks_like_unapplied_code_change(response_content, last_user_message="", min_lines=5):
    """
    Detects a fenced code block that looks like a proposed edit (not a trivial
    illustrative snippet) in a response that produced NO tool call — but only when
    the user's own request didn't explicitly ask to just see the code.
    """
    # 1. Check if user explicitly asked *not* to apply
    if NO_APPLY_REQUESTED_PATTERNS.search(last_user_message):
        return False

    # 2. Look for code blocks that meet the minimum line threshold
    for m in re.finditer(r"```[a-zA-Z]*\n(.*?)\n```", response_content, re.DOTALL):
        block = m.group(1)
        if block.count("\n") + 1 >= min_lines:
            return True

    return False


def check_unused_local_function_shadowed_by_wrong_call(filepath):
    """
    Flags a nested/local function that is defined but never called, when a
    higher-scope function with a different name is called with arguments that
    don't match its own signature — a strong signal the model meant to call
    the unused local function instead.
    """
    import ast
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # collect nested function defs and all Call nodes within this function's body
        nested_defs = {n.name: n for n in ast.walk(node) if isinstance(n, ast.FunctionDef) and n is not node}
        calls_by_name = {}
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                calls_by_name.setdefault(n.func.id, []).append(n)

        for name, def_node in nested_defs.items():
            called = any(n.func.id == name for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name))
            if called:
                continue
            # nested function is unused — check if the OUTER function is called
            # with an arity matching the unused nested function instead of its own
            outer_calls = calls_by_name.get(node.name, [])
            expected_arity = len(def_node.args.args)
            for call in outer_calls:
                if len(call.args) == expected_arity and expected_arity != len(node.args.args):
                    return [
                        f"Line {call.lineno}: '{node.name}(...)' is called with "
                        f"{len(call.args)} argument(s), matching the unused nested "
                        f"function '{name}' defined at line {def_node.lineno}, not "
                        f"'{node.name}'s own signature. You likely meant to call "
                        f"'{name}(...)' instead."
                    ]
    return []
