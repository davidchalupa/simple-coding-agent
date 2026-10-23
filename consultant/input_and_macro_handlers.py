import sys
import os
import re
import glob
import time

from common.context_hygiene_utils import get_tokens_used

MODE_RE = re.compile(r'^/mode\b\s*(code|think)', re.IGNORECASE)


def handle_user_input(state, user_input, system_prompt, switcher):
    if user_input == "/quit":
        print("Exiting. Goodbye!")
        sys.exit(0)

    if user_input == "/clear":
        state.messages = [{"role": "system", "content": system_prompt}]
        state.session_cwd = os.getcwd()
        state.consult_read_cache = {}
        state.expect_plain_text = False
        state.answering_violations = 0
        state.last_directive = None
        state.consecutive_reading_turns = 0

        # === Clear KV cache files for current model(s) ===
        if switcher.cache_dir and os.path.exists(switcher.cache_dir):
            pattern1 = f"{state.primary_model_key}_*.bin"
            pattern2 = f"{state.reasoning_model_key or 'reasoner'}_*.bin"

            for pattern in [pattern1, pattern2]:
                cache_files = glob.glob(os.path.join(switcher.cache_dir, pattern))
                for cf in cache_files:
                    try:
                        os.remove(cf)
                        print(f"🗑️  Deleted KV cache file: {os.path.basename(cf)}")
                    except Exception as e:
                        print(f"⚠️  Failed to delete {cf}: {e}")

        print("🧹 Memory and environment completely cleared!")
        return True

    if user_input == "/cancel":
        print("❌ Current draft discarded.")
        return True

    if not user_input:
        return True

    return False


def show_stats(state, switcher):
    """Display telemetry and context hygiene stats"""
    total_tokens = get_tokens_used(state.messages, switcher.llm)
    context_window = getattr(switcher, 'context_window', None) or 32768
    usage_pct = (total_tokens / context_window * 100) if context_window else 0.0
    headroom = context_window - total_tokens

    # --- Token Breakdown by Category ---
    system_tokens = 0
    tool_tokens = 0
    chat_tokens = 0

    for msg in state.messages:
        role = msg.get("role")
        content = msg.get("content", "")
        # Rough token estimate (~4 chars per token) if switcher tokenizer isn't used per-message
        msg_len = len(content) // 4

        if role == "system":
            system_tokens += msg_len
        elif role == "user" and "Tool Execution Results:" in content:
            tool_tokens += msg_len
        else:
            chat_tokens += msg_len

    # --- Disk Cache Check ---
    cache_dir = switcher.cache_dir
    disk_cache_files = []
    total_disk_size_mb = 0

    if cache_dir and os.path.exists(cache_dir):
        pattern = f"{state.primary_model_key}_*.bin"
        existing_cache_files = glob.glob(os.path.join(cache_dir, pattern))
        for cf in existing_cache_files:
            try:
                size_mb = os.path.getsize(cf) / (1024 * 1024)
                disk_cache_files.append((os.path.basename(cf), size_mb))
                total_disk_size_mb += size_mb
            except Exception:
                pass

    print("\n📊 [Stats & Context Hygiene]")
    print(f"   Mode: {state.active_mode.upper()}")
    print(f"   Messages: {len(state.messages)} turns")
    print(f"   Context Used: ~{total_tokens:,} / {context_window:,} tokens ({usage_pct:.1f}%)")
    print(f"   Headroom Left: ~{headroom:,} tokens")

    print("\n📦 [Context Breakdown]")
    print(f"   • Loaded Code / Tool Results : ~{tool_tokens:,} tokens ({tool_tokens / max(1, total_tokens) * 100:.1f}%)")
    print(f"   • Conversation History       : ~{chat_tokens:,} tokens ({chat_tokens / max(1, total_tokens) * 100:.1f}%)")
    print(f"   • System Instructions        : ~{system_tokens:,} tokens ({system_tokens / max(1, total_tokens) * 100:.1f}%)")

    if disk_cache_files:
        latest_cache = sorted(disk_cache_files, reverse=True)[0]
        print(f"\n💾 [KV Cache Persistence]")
        print(f"   • Active Disk Cache : {latest_cache[0]} ({latest_cache[1]:.2f} MB)")
        print(f"   • Total Cache Space : {total_disk_size_mb:.2f} MB")

    # Generation Speed
    if hasattr(state, 'last_turn_timestamp') and state.last_turn_timestamp:
        elapsed = time.time() - state.last_turn_timestamp
        tokens_per_sec = len(state.messages[-1].get("content", "")) / elapsed if elapsed > 0 else 0
        if tokens_per_sec > 0:
            print(f"\n⚡ Last Generation Speed: {tokens_per_sec:.2f} tok/s")

    return True


def handle_macros(state, user_input, coder_system_prompt, reasoner_system_prompt, switcher):
    # === Handle /stats command ===
    if user_input == '/stats':
        show_stats(state, switcher)
        return True

    # --- Handle Mode Switching ---
    mode_match = MODE_RE.match(user_input.strip())
    if mode_match:
        new_mode = mode_match.group(1).lower()

        if new_mode == "think" and not state.reasoning_model_key:
            print("⚠️  No reasoning model configured. Operating in code mode.")
            return True

        if new_mode != state.active_mode:
            state.active_mode = new_mode
            try:
                if state.active_mode == "think":
                    switcher.load(state.reasoning_model_key)
                    state.messages[0] = {"role": "system", "content": reasoner_system_prompt}
                    print(f"\n🧠 [Reasoning Mode Active] Switched to {switcher.display_name}")
                else:
                    switcher.load(state.primary_model_key)
                    print(f"\n💻 [Coding Mode Active] Switched to {switcher.display_name}")
            except Exception as e:
                print(f"\n❌ Failed to switch models: {e}")
        else:
            print(f"Already in {new_mode} mode.")

        # Standalone command: do not process further, wait for next user prompt
        return True

    return False
