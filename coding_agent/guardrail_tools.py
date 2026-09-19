import os
import json
import re
import ast

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


import sys

def check_import_resolution(filepath, session_cwd):
    import ast
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    search_dir = os.path.dirname(os.path.abspath(filepath))
    stdlib_names = getattr(sys, "stdlib_module_names", set())

    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            top_level = node.module.split(".")[0]
            if top_level in stdlib_names:
                continue
            module_file = os.path.join(search_dir, top_level + ".py")
            module_pkg = os.path.join(search_dir, top_level, "__init__.py")
            if not os.path.isfile(module_file) and not os.path.isfile(module_pkg):
                errors.append(f"from {node.module} import ... — no file '{top_level}.py' found in {search_dir}")
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


import ast

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

    search_dir = os.path.dirname(os.path.abspath(test_filepath))

    # 1. Build a small index: for every function defined anywhere in search_dir,
    #    record its own parameter names, so we know e.g. run_game_loop's 5th
    #    parameter is called `get_action`.
    func_params = {}   # func_name -> [param_names]
    call_arities = {}  # param_name (as used INSIDE a function body) -> observed call arity

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
                func_params[node.name] = [a.arg for a in node.args.args]
                # look for calls to any of this function's OWN parameters inside its body
                # (i.e. it treats one of its params as a callback and invokes it)
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                            and inner.func.id in node.args.args):
                        arity = len(inner.args) + len(inner.keywords)
                        call_arities[inner.func.id] = arity  # last-writer wins; fine for single-callback-name codebases

    if not call_arities:
        return []

    # 2. In the test file, find calls where a lambda/function is passed for a parameter
    #    name matching one of the callback names we found being invoked.
    with open(test_filepath, "r", encoding="utf-8") as f:
        test_tree = ast.parse(f.read(), filename=test_filepath)

    errors = []
    for node in ast.walk(test_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            callee_params = func_params.get(node.func.id)
            if not callee_params:
                continue

            # match positional args to callee's declared param names
            for i, arg in enumerate(node.args):
                if isinstance(arg, ast.Lambda) and i < len(callee_params):
                    pname = callee_params[i]
                    if pname in call_arities and len(arg.args.args) != call_arities[pname]:
                        errors.append(
                            f"Line {arg.lineno}: lambda passed for '{pname}' takes "
                            f"{len(arg.args.args)} argument(s), but '{pname}' is invoked "
                            f"with {call_arities[pname]} argument(s) inside {node.func.id}. "
                            f"Adjust the lambda's parameter count to match."
                        )
            for kw in node.keywords:
                if isinstance(kw.value, ast.Lambda) and kw.arg in call_arities:
                    if len(kw.value.args.args) != call_arities[kw.arg]:
                        errors.append(
                            f"Line {kw.value.lineno}: lambda passed for '{kw.arg}=' takes "
                            f"{len(kw.value.args.args)} argument(s), but '{kw.arg}' is invoked "
                            f"with {call_arities[kw.arg]} argument(s) inside {node.func.id}. "
                            f"Adjust the lambda's parameter count to match."
                        )
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
    r"\b(just (show|output|print|display)|"
    r"don'?t (write|apply|save|modify|create)|"
    r"in a (markdown|code) block|"
    r"without (writing|applying|saving)|"
    r"show me the code|"
    r"no need to (write|apply|save))\b",
    re.IGNORECASE,
)

def looks_like_unapplied_code_change(response_content, last_user_message="", min_lines=5):
    """
    Detects a fenced code block that looks like a proposed edit (not a trivial
    illustrative snippet) in a response that produced NO tool call — but only when
    the user's own request didn't explicitly ask to just see the code.
    """
    if NO_APPLY_REQUESTED_PATTERNS.search(last_user_message):
        return False

    for m in re.finditer(r"```[a-zA-Z]*\n(.*?)\n```", response_content, re.DOTALL):
        block = m.group(1)
        if block.count("\n") + 1 >= min_lines:
            return True
    return False