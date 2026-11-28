import os
import re
from coding_agent.guardrails.tools import looks_like_unapplied_code_change


def check_and_handle_unread_replace_lines(tool_name, tool_args, state, agent_flags):
    """
    Prevents replace_lines from executing if the file was not inspected
    in recent turns via read_file or read_symbol.
    """
    if tool_name != "replace_lines":
        return False

    target_fp = tool_args.get("filepath", "")
    target_filename = os.path.basename(target_fp)

    # Check recent messages for a read_file/read_symbol call on this file
    recent_messages_threshold = 16
    recent_messages = state.messages[-recent_messages_threshold:]
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

        state.messages.append({
            "role": "user",
            "content": (
                f"System Alert: `replace_lines` on '{target_fp}' was BLOCKED because you have not inspected "
                f"this file recently. Call `read_symbol` or `read_file` if you need current line numbers "
                f"before editing."
            )
        })
        return True

    return False


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


def inject_pure_load_request_nudge(state, user_input):
    state.messages.append({
        "role": "user",
        "content": (
            f"System Alert: Pure file loading request detected.\n\n"
            f"User said: '{user_input}'\n\n"
            "You MUST use `read_file` or `read_symbol` tool to load this content. \n"
            "- For entire files: Use read_file with filepath and max_lines=-1\n"
            "- For specific symbols/functions: Use read_symbol with filepath and symbol_name\n"
            f"\nDO NOT output generic instructions like 'I can help you...' without first making a tool call."
        )
    })

def inject_pure_load_or_inspect_request_nudge(state, user_input):
    state.messages.append({
        "role": "user",
        "content": (
            f"System Alert: Read-only inspection request detected.\n\n"
            f"User said: '{user_input}'\n\n"
            "You MUST call the requested tool (`read_file` or `read_symbol`) to inspect the target.\n"
            "CRITICAL: This is a strictly read-only request. Once you receive the tool output, "
            "acknowledge it with a brief summary or confirmation. **DO NOT** output code blocks, "
            "unsolicited code rewrites, or refactoring solutions."
        )
    })
