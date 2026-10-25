import os
import glob
import time

from pathlib import Path

from common.input_handler import get_user_prompt
from common.context_hygiene_utils import render_token_footer

from consultant.system_prompt_builder import build_consultant_system_prompt, build_diagnose_system_prompt
from consultant.input_and_macro_handlers import handle_user_input, handle_macros
from consultant.model_switcher import ModelSwitcher

from consultant.engine import run_consultant_turn

from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY

MAX_ANSWERING_VIOLATIONS_PER_TURN = 3


class ConsultantState:
    def __init__(self, parsed_args):
        self.messages = []
        self.session_cwd = os.getcwd()
        self.consult_read_cache = {}
        self.forbidden_tools = frozenset({"write_file", "append_file", "patch_file", "replace_lines"})
        self.max_tool_calls_per_turn = 20

        self.primary_model_key = parsed_args["model"]
        self.reasoning_model_key = parsed_args["reasoning_model"]
        self.kv_quantization_type = parsed_args["kv_quantization_type"]
        self.models_dir = Path(__file__).resolve().parent / "models"

        # Enables KV Cache persistence
        self.cache_persistence_enabled = True

        # Tracks the active state: "code" (default) or "think"
        self.active_mode = "code"

        self.expect_plain_text = False
        self.answering_violations = 0
        self.last_directive = None

        # === NEW: Reading round safety cap tracking ===
        self.consecutive_reading_turns = 0
        self.max_consecutive_reading_turns = 3

        # stats / telemetry
        self.last_turn_timestamp = None  # Track when last response started
        self.turn_start_tokens = 0  # Token count at start of turn


parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
state = ConsultantState(parsed_args)


def summarize_loaded_targets(tool_requests):
    parts = []
    for req in tool_requests:
        name = req.get("name")
        args = req.get("args", {}) or {}
        filepath = args.get("filepath")
        symbol = args.get("symbol_name")
        dirpath = args.get("dir_path")
        if name == "read_symbol" and filepath and symbol:
            parts.append(f"`{symbol}` from `{os.path.basename(filepath)}`")
        elif filepath:
            parts.append(f"`{os.path.basename(filepath)}`")
        elif dirpath:
            parts.append(f"the directory listing for `{dirpath}`")

    seen = set()
    unique_parts = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique_parts.append(p)

    return ", ".join(unique_parts) if unique_parts else "the requested content"


def execute_prepare_macro(state, coder_system_prompt, reasoner_system_prompt, switcher, macro_file_path):
    """Execute a prepare workflow from file using builtins.input patching"""

    if not os.path.exists(macro_file_path):
        print(f"\n❌ [Prepare] File not found: {macro_file_path}")
        return False

    try:
        with open(macro_file_path, 'r', encoding='utf-8') as f:
            macro_content = f.read()

        input_queue = [line.strip() for line in macro_content.split('\n')
                       if line.strip() and not line.strip().startswith('#')]

    except Exception as e:
        print(f"\n❌ [Prepare] Failed to read macro file: {e}")
        return False

    original_cwd = os.getcwd()
    state.consult_read_cache.clear()

    try:
        safety_counter = {"calls": 0, "max_calls": len(input_queue) + 20}
        original_len = len(input_queue)

        def smart_input_mocker(prompt=""):
            safety_counter["calls"] += 1
            if safety_counter["calls"] > safety_counter["max_calls"]:
                print("\n🛑 [Prepare Overload] Too many input calls. Forcing exit.", flush=True)
                return "/quit"

            prompt_str = str(prompt).lower()

            # Auto-approve tool confirmation prompts (same logic as your test runner)
            if "allow" in prompt_str or "y/n" in prompt_str or "execute" in prompt_str or "?" in prompt_str:
                print(f"\n🤖 [Prepare] Auto-approving prompt ({prompt.strip()}): 'y'", flush=True)
                return "y"

            # Pull next command from the macro file queue
            if input_queue:
                next_input = input_queue.pop(0)
                step_num = original_len - len(input_queue)
                print(f"\n⌨️ [Prepare Step {step_num}/{original_len}] Typing: {next_input}", flush=True)
                return next_input

            return "/quit"

        from unittest.mock import patch

        # Patch builtins.input directly, matching your test runner architecture
        with patch("builtins.input", side_effect=smart_input_mocker):
            try:
                main_loop(state, coder_system_prompt, reasoner_system_prompt, switcher)
            except SystemExit as e:
                print(f"\n[Prepare] Process exited with code {e.code}")
            except Exception as e:
                print(f"\n❌ [Prepare] Unexpected error in workflow execution: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()

    finally:
        os.chdir(original_cwd)

    print("\n✅ [Prepare] Workflow completed. Context is now ready for interactive use.")
    return True


def handle_prepare_macro(state, user_input, coder_system_prompt, reasoner_system_prompt, switcher):
    """/prepare macro has a separate handler because is execution requires calling main_loop internally"""
    # Check for /prepare command here ===
    if user_input.startswith('/prepare '):
        macro_file_path = user_input[len('/prepare'):].strip()

        print(f"\n💬 [Consult] Executing the prepare instructions from {macro_file_path}")

        # Execute prepare workflow and return True to exit main loop
        if execute_prepare_macro(state, coder_system_prompt, reasoner_system_prompt, switcher, macro_file_path):
            print("\n💬 [Consult] Awaiting your next question.")
            return True

    return False


def main_loop(state, coder_system_prompt, reasoner_system_prompt, switcher):
    while True:
        user_input = get_user_prompt()

        if handle_user_input(state, user_input, coder_system_prompt, switcher):
            continue

        if handle_macros(state, user_input, coder_system_prompt, reasoner_system_prompt, switcher):
            continue

        if handle_prepare_macro(state, user_input, coder_system_prompt, reasoner_system_prompt, switcher):
            continue

        # Ensure correct model is loaded for the active mode
        active_model_key = state.reasoning_model_key if state.active_mode == "think" else state.primary_model_key

        # === Save KV cache before switching models or starting fresh session ===
        if state.cache_persistence_enabled and switcher.cache_dir and os.path.exists(switcher.cache_dir):
            pattern = f"{active_model_key}_*.bin"
            existing_cache_files = glob.glob(os.path.join(switcher.cache_dir, pattern))

            # Delete old cache files for this model before saving new one
            for cf in existing_cache_files:
                try:
                    os.remove(cf)
                except Exception as e:
                    print(f"⚠️  Failed to delete stale cache {cf}: {e}")

        llm, context_window = switcher.load(active_model_key)
        is_reasoner = (state.active_mode == "think")

        # === Save KV cache after model load/start ===
        if state.cache_persistence_enabled and switcher.cache_dir and os.path.exists(switcher.cache_dir):
            cache_filename = f"{active_model_key}_{int(time.time())}.bin"
            cache_file_path = os.path.join(switcher.cache_dir, cache_filename)
            switcher.save_current_context(cache_file_path)

        # === Restore KV cache if available ===
        if state.cache_persistence_enabled and switcher.cache_dir and os.path.exists(switcher.cache_dir):
            pattern = f"{active_model_key}_*.bin"
            existing_cache_files = glob.glob(os.path.join(switcher.cache_dir, pattern))
            if len(existing_cache_files) > 0:
                existing_cache_files.sort(reverse=True)
                latest_cache_file = existing_cache_files[0]
                if switcher.restore_previous_context(latest_cache_file):
                    try:
                        os.remove(latest_cache_file)
                    except Exception as e:
                        print(f"⚠️  Failed to cleanup old cache after restore: {e}")

        try:
            run_consultant_turn(
                state=state,
                user_input=user_input,
                llm=llm,
                context_window=context_window,
                is_reasoner=is_reasoner
            )
        except Exception as e:
            print(f"\n[Error during generation]: {e}")
        finally:
            render_token_footer(state.messages, llm, context_window)
            state.last_turn_timestamp = None
            state.turn_start_tokens = 0


def main(state):
    coder_system_prompt = build_consultant_system_prompt()
    reasoner_system_prompt = build_diagnose_system_prompt()
    switcher = ModelSwitcher(state.kv_quantization_type, state.models_dir)
    switcher.load(state.primary_model_key)

    # === NEW: Initialize cache directory once at startup ===
    if state.cache_persistence_enabled:
       switcher.ensure_cache_dir_exists()

    state.messages = [{"role": "system", "content": coder_system_prompt}]

    print(f"\n🔍 [Coding Consultant] {switcher.display_name} loaded. Read-only — write tools are disabled.")
    if state.reasoning_model_key is not None:
        reasoning_display = MODEL_REGISTRY[state.reasoning_model_key]["display_name"]
        print(
            f"💡 Multi-Model mode enabled: Type '/mode think' to switch to {reasoning_display}, and '/mode code' to switch back.")
    print()

    main_loop(state, coder_system_prompt, reasoner_system_prompt, switcher)


if __name__ == "__main__":
    main(state)
