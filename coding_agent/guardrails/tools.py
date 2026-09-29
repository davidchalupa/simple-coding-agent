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


def auto_heal_newline_escaping(fp):
    """
    This is an absolute last resort in trying to auto-heal buggy escaping of newline characters
    by Qwen inside of tool call jsons that have been observed multiple times and the LLM is unresponsive to
    behavioral fixes.
    """
    with open(fp, "r", encoding="utf-8") as f:
        file_lines = f.readlines()

    healed = False
    new_lines = []
    i = 0
    while i < len(file_lines):
        line = file_lines[i]
        # Detect broken print statements split by JSON parsing
        if 'print("' in line and not line.strip().endswith(
                '")') and not line.strip().endswith('\\'):
            if i + 1 < len(file_lines):
                next_line = file_lines[i + 1].lstrip()
                # Merge them using an actual escaped \n
                merged = line.rstrip('\r\n') + '\\n' + next_line
                new_lines.append(merged)
                i += 2
                healed = True
                continue
        new_lines.append(line)
        i += 1
    return healed, new_lines


def find_last_code_block(messages):
    """Scan backwards through assistant turns for the most recent fenced code block."""
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            match = re.search(r"```(?:python)?\s*\n(.*?)\n```", msg["content"], re.DOTALL)
            if match:
                return match.group(1)
    return None


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


def check_and_handle_unread_replace_lines(tool_name, tool_args, state, agent_flags):
    """
    Prevents replace_lines from executing if the file was not inspected
    in recent turns via read_file or read_symbol.
    """
    if tool_name != "replace_lines":
        return False

    target_fp = tool_args.get("filepath", "")
    target_filename = os.path.basename(target_fp)

    # Check the last 8 messages for a read_file/read_symbol call on this file
    recent_messages = state.messages[-8:]
    file_was_read = False

    for msg in recent_messages:
        content_str = str(msg.get("content", ""))

        # Check if an assistant message issued a read call for this file
        if msg.get("role") in ("assistant", "model"):
            if ("read_file" in content_str or "read_symbol" in content_str) and target_filename in content_str:
                file_was_read = True
                break

    if not file_was_read:
        print(f"🛡️  [Guardrail] Blocked replace_lines on '{target_filename}' — file was not read recently.")
        agent_flags.consecutive_errors += 1

        state.messages.append({
            "role": "user",
            "content": (
                f"System Alert: `replace_lines` on '{target_fp}' was BLOCKED because you have not inspected "
                f"this file recently. You MUST call `read_symbol` or `read_file` first to get exact, updated "
                f"line numbers and anchor snippets before making edits."
            )
        })
        return True

    return False


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


def check_and_handle_drastic_shrinkage(tool_args, state, agent_flags, content_key, threshold=0.5):
    """
    Blocks a write_file that removes more than `threshold` fraction of the existing file's
    lines, unless the file is trivially small. Catches 'fix one error by deleting everything'
    regressions that check_and_handle_identical_write cannot (content is NOT identical, so
    that guardrail doesn't fire — this one's job is drastic, not identical, changes).
    """
    target_fp = tool_args.get("filepath", "")
    if not os.path.isfile(target_fp):
        return False

    try:
        with open(target_fp, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()

        proposed_content = tool_args.get(content_key, "")
        proposed_lines = proposed_content.splitlines(keepends=True)

        if len(existing_lines) >= 5 and len(proposed_lines) < len(existing_lines) * (1 - threshold):
            print(
                f"🛡️  [Guardrail] Blocked drastic shrinkage of '{os.path.basename(target_fp)}' "
                f"({len(existing_lines)} → {len(proposed_lines)} lines)."
            )

            agent_flags.consecutive_errors += 1
            if agent_flags.consecutive_errors >= 3:
                print("🛑 [Circuit Breaker] Agent stuck attempting to shrink the file. Forcing turn end.")
                return True

            alert_msg = (
                f"System Alert: `write_file` blocked — this write would shrink '{target_fp}' from "
                f"{len(existing_lines)} lines to {len(proposed_lines)} lines "
                f"({len(existing_lines) - len(proposed_lines)} lines removed). "
                f"If you are fixing a specific error (e.g. a bad import), make a SCOPED fix — "
                f"correct only the broken line(s) and preserve all existing test methods and "
                f"logic. Do not delete content while fixing an unrelated error. If you intend "
                f"to genuinely replace most of the file, explain why in your reasoning first."
            )
            state.messages.append({"role": "user", "content": alert_msg})
            return True
    except Exception:
        pass
    return False

NO_APPLY_REQUESTED_PATTERNS = re.compile(
    r"\b("
    # Catch "just / only write / show / draft"
    r"(just|only)\s+(show|output|print|display|write(\s+down)?|provide|draft|plan)|"
    # Catch "don't / do not modify / apply / make changes"
    r"(do\s+not|don'?t)\s+(write|apply|save|modify|edit|change|make\s+(any\s+)?(modifications|changes|edits))|"
    # Catch "without applying / modifying"
    r"without\s+(writing|applying|saving|modifying|editing|making\s+changes)|"
    # Catch explicit markdown/code requests
    r"in a (markdown|code) block|"
    r"(show|give)\s+(me\s+)?the code|"
    r"no need to (write|apply|save|modify|edit)"
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


def check_and_handle_identical_write(tool_args, state, agent_flags, content_key):
    """
    Checks if the proposed content is identical to the existing content in the file.
    If identical, blocks the write operation and provides a guardrail message.
    Returns (intercepted: bool, should_break: bool)
    """
    target_fp = tool_args.get("filepath", "")
    if not target_fp or not os.path.isfile(target_fp):
        return False, False

    try:
        with open(target_fp, "r", encoding="utf-8") as f:
            existing_disk_content = f.read()

        proposed_content = tool_args.get(content_key, "")

        # 1. TIGHTER COMPARISON: Normalize line endings and trailing line spaces
        def normalize_code(code_str):
            code_str = code_str.replace('\r\n', '\n')
            return "\n".join(line.rstrip() for line in code_str.splitlines()).strip()

        if normalize_code(existing_disk_content) == normalize_code(proposed_content):
            print(
                f"🛡️  [Guardrail] Blocked identical write_file to '{os.path.basename(target_fp)}' (No-Op Regurgitation).")
            agent_flags.record_hit("identical_write", state.track_guardrail_hits)

            agent_flags.consecutive_errors += 1
            if agent_flags.consecutive_errors >= 3:
                print("🛑 [Circuit Breaker] Agent stuck in identical write loop. Forcing turn end.")
                return True, True  # intercepted, SHOULD BREAK

            # 2. PATH NORMALIZATION: Ensure relative/absolute paths match correctly
            target_abs = os.path.abspath(target_fp)
            last_fail = agent_flags.last_verification_failure or {}
            last_fail_abs = os.path.abspath(last_fail.get("filepath", "")) if last_fail.get("filepath") else ""

            # 3. THE "JOLT" MESSAGES: Force the agent to realize it didn't change the payload
            if last_fail_abs == target_abs:
                alert_msg = (
                    f"System Alert: `write_file` blocked (No-Op Regurgitation).\n"
                    f"You attempted to overwrite '{target_fp}' with the EXACT SAME BROKEN CODE that just failed self-verification.\n"
                    f"Although you may have diagnosed the problem in your text, you failed to actually change the tool payload.\n\n"
                    f"Error was:\n{last_fail.get('error', '')}\n\n"
                    f"ACTION REQUIRED: Do not resubmit the same file. Actually implement the fix and issue a new tool call."
                )
                if "unterminated string literal" in last_fail.get("error", ""):
                    alert_msg += "\n\nHint: If you used '\\n' inside a Python string, the JSON parser converted it to a real newline. Avoid using '\\n' in string literals (e.g. use multiple prints) or quadruple-escape it as `\\\\n`."

            elif getattr(agent_flags, "awaiting_fix", False) and getattr(agent_flags, "last_run_cmd_error", None):
                alert_msg = (
                    f"System Alert: `write_file` blocked (No-Op Regurgitation).\n"
                    f"You submitted code that is IDENTICAL to the file that just failed with this error:\n{agent_flags.last_run_cmd_error}\n\n"
                    f"The file is NOT fixed. Do not claim success or say the file 'already contains the correct content'. "
                    f"You likely diagnosed the issue in your thoughts but forgot to apply the change to the `content` argument.\n"
                    f"ACTION REQUIRED: Make a REAL change to fix the error."
                )
                if any(sig in agent_flags.last_run_cmd_error for sig in ("ModuleNotFoundError", "ImportError")):
                    alert_msg += (
                        "\n\nHint: this is a missing-module error. Do not guess a module name by analogy to a "
                        "function name (e.g. assuming `foo_get_action` lives in `action_foo_agent.py`). "
                        "If you search the codebase for the function name, use a query like `def random_get_action` "
                        "(not just the bare name) to find the DEFINITION site specifically — a bare name search "
                        "will also match your own broken import line and may hide the real result. Also use a "
                        "higher max_matches (e.g. 5) since the same name can appear in multiple places."
                    )

            else:
                alert_msg = (
                    f"System Alert: `write_file` on '{target_fp}' was blocked because the new content is IDENTICAL to the existing file on disk.\n"
                    f"If the user only asked to read, analyze, inspect, or explain, DO NOT invoke write tools. Answer directly in plain text."
                )

            state.messages.append({
                "role": "user",
                "content": alert_msg
            })
            return True, False  # intercepted, keep retrying

    except Exception:
        pass

    return False, False


def check_and_handle_loop_guardrail(tool_name, tool_args, state, agent_flags):
    """
    Checks if the same tool call has been attempted recently and blocks it.
    Returns (intercepted: bool, should_break: bool)
    """
    curr_sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
    agent_flags.recent_tool_signatures.append(curr_sig)
    agent_flags.recent_tool_signatures = agent_flags.recent_tool_signatures[-6:]  # short rolling window

    repeat_count = agent_flags.recent_tool_signatures.count(curr_sig)
    if repeat_count >= 2:
        agent_flags.record_hit("loop_guardrail", state.track_guardrail_hits)
        agent_flags.consecutive_errors += 1

        if agent_flags.consecutive_errors >= 3:
            print("🛑 [Circuit Breaker] Agent loop detected. Forcing turn end.")
            alert_msg = (
                f"🛑 CRITICAL SYSTEM INTERVENTION: You have attempted the exact same '{tool_name}' tool call "
                f"{repeat_count} times without changing parameters or fixing errors. Tool execution is HALTED.\n"
                f"DO NOT issue another tool call. Stop calling tools now and explain in plain text what went wrong and what step you will take next."
            )
            state.messages.append({"role": "user", "content": alert_msg})
            return True, True  # (intercepted=True, should_break=True)

        alert_msg = (
            f"System Alert: This exact tool call has been attempted {repeat_count} times "
            f"recently and is not succeeding. Do not repeat it verbatim — either fix the "
            f"underlying issue (e.g. re-check content/context requirements) or try a different approach."
        )
        state.messages.append({"role": "user", "content": alert_msg})
        return True, False  # (intercepted=True, should_break=False)

    return False, False


READ_INTENT_PATTERN = re.compile(
    r'\b(read|inspect|view|examine|look at)\b.*\b(file|code|contents?)\b',
    re.IGNORECASE,
)
WRITE_INTENT_PATTERN = re.compile(
    r'\b(write|save)\b.*\b(file|code|contents?)\b',
    re.IGNORECASE,
)

def looks_like_memory_regurgitation_on_read_request(response_content, last_user_message, tool_calls_this_turn, min_lines=5):
    """
    Detects the specific pattern: the user asked to read/inspect a file, no tool has
    executed yet this turn, and the response contains a large code block — almost
    certainly the model reciting something from memory instead of calling read_file.
    """
    if tool_calls_this_turn > 0:
        return False
    if not READ_INTENT_PATTERN.search(last_user_message or ""):
        return False
    if WRITE_INTENT_PATTERN.search(last_user_message or ""):
        return False
    return looks_like_unapplied_code_change(response_content, last_user_message="")
    # last_user_message="" deliberately bypasses the "just show me the code" suppression
    # from looks_like_unapplied_code_change, since THIS check is only reached when the
    # user's message already matched a read-file intent, not a "show me" intent.


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
