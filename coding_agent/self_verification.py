import os
import re

from . import native_linter


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
