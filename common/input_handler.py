import sys
import os
import builtins
from pathlib import Path

# Encapsulated state just for the prompt session
_prompt_session = None

def is_interactive_session() -> bool:
    """Detects if we are running in a real terminal vs an automated test."""
    if not (getattr(sys.stdin, 'isatty', lambda: False)() and getattr(sys.stdout, 'isatty', lambda: False)()):
        return False
    if hasattr(builtins.input, "__wrapped__") or "mock" in type(builtins.input).__name__.lower():
        return False
    return True

def _fallback_input() -> str:
    """The legacy input loop, 100% compatible with existing automated tests."""
    print("\n[You] (Type /send to submit, /cancel to scratch draft, /undo to delete last line):")
    user_lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break

        trimmed = line.strip()
        if trimmed == "/send":
            break
        if trimmed in ["/quit", "/clear", "/cancel"]:
            return trimmed
        if trimmed == "/undo":
            if user_lines:
                removed = user_lines.pop()
                print(f"🗑️  Removed line: \"{removed}\"")
            else:
                print("⚠️ Buffer is already empty.")
            continue

        user_lines.append(line)
    return "\n".join(user_lines).strip()

def get_user_prompt() -> str:
    """Smart input handler: Returns prompt_toolkit in TTY, falls back to raw input() for tests."""
    global _prompt_session

    if not is_interactive_session():
        return _fallback_input()

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
    except ImportError:
        print("\n⚠️  'prompt_toolkit' not found. Falling back to basic input.")
        return _fallback_input()

    if _prompt_session is None:
        bindings = KeyBindings()

        @bindings.add('enter')
        def _(event):
            buffer = event.app.current_buffer
            current_line = buffer.document.current_line.strip()
            full_text = buffer.text.strip()
            instant_commands = ['/quit', '/clear', '/cancel']
            macro_prefixes = ('/readme', '/requirements', '/split')

            if current_line == '/send':
                event.current_buffer.validate_and_handle()
            elif full_text in instant_commands:
                event.current_buffer.validate_and_handle()
            elif full_text.startswith(macro_prefixes) and '\n' not in full_text:
                event.current_buffer.validate_and_handle()
            else:
                buffer.insert_text('\n')

        @bindings.add('escape', 'enter')
        def _(event):
            event.current_buffer.validate_and_handle()

        history_file = os.path.expanduser("~/.coding_agent_history")
        Path(history_file).touch(exist_ok=True)

        _prompt_session = PromptSession(
            history=FileHistory(history_file),
            key_bindings=bindings,
            multiline=True
        )

    print("\n[You] (Alt+Enter or type /send on a new line to submit):")
    try:
        user_text = _prompt_session.prompt("> ")
    except (EOFError, KeyboardInterrupt):
        return "/quit"

    lines = user_text.split('\n')
    if lines and lines[-1].strip() == '/send':
        lines.pop()

    return "\n".join(lines).strip()
