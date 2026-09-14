import os
import json
import re

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
