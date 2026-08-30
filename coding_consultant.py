import sys
import os
import json
import psutil

import llama_cpp
from llama_cpp import Llama
from pathlib import Path

from coding_agent.input_handler import get_user_prompt
from coding_agent.execute_tool import execute_tool
from coding_agent import payload_parser
from coding_agent.guardrail_tools import stream_agent_response
from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY

# --- CLI / MODEL SETUP ---
parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
active_config = MODEL_REGISTRY[parsed_args["model"]]

target_path = Path(__file__).resolve().parent / "models" / active_config["filename"]
loaded_model_name = active_config["display_name"]
CONTEXT_WINDOW = active_config["max_context"]

# --- GLOBAL STATE ---
# Deliberately no is_split_mode / is_execute_mode / sandbox_directory / ALLOW_PATCH /
# FORCE_TESTING / SELF_VERIFY_PY_WRITES here. This script is read-only, single-purpose,
# and none of that agent-mode machinery can ever be reached from it.
llm = None
SYSTEM_PROMPT = ""
messages = []
session_cwd = os.getcwd()
consult_read_cache = {}  # {(tool_name, filepath, symbol_name, start_line, max_lines): tool_result}

# Tool names that must never be reachable from consult mode. Kept as a defense-in-depth
# guardrail even though the parser is always called with allow_patch=False here — if a
# future shared-file change to payload_parser ever starts recognizing these regardless
# of that flag, this catches it instead of silently executing a write.
WRITE_TOOLS = frozenset({"write_file", "append_file", "patch_file", "replace_lines"})

# Consult mode allows at most this many REAL tool executions per user question before
# forcing a text-only answer from whatever context already exists. Cache hits don't
# count against this (they're free), but they do feed the loop-detection signature list
# below so a model that ignores cached content still gets cut off deterministically.
MAX_TOOL_CALLS_PER_TURN = 1


def get_system_ram_gb():
    """Returns total system RAM in gigabytes."""
    return psutil.virtual_memory().total / (1024 ** 3)


def build_consultant_system_prompt():
    """
    Builds the system prompt for the read-only coding consultant.

    Deliberately NOT shared with coding_agent's build_system_prompt. Consultant-specific
    prompt tuning (anti-over-fetching language, cache-awareness instructions) caused
    hard-to-reproduce, probabilistic regressions when it lived behind a read_only flag
    inside the agent's shared prompt builder — a wording change made for consult mode
    could shift agent-mode behavior in subtle ways, and vice versa. Keeping this fully
    separate means changes here can never destabilize the write-capable agent.
    """
    tools_section = (
        '1. `list_tree`: {"dir_path": "<str>", "max_depth": <int>} - Explores and visualizes directory structures.\n    '
        '2. `search_codebase`: {"dir_path": "<str>", "query": "<str>", "is_regex": <bool>, "max_matches": <int>} - Greps for strings or regex across non-binary files.\n    '
        '3. `read_file`: {"filepath": "<str>", "start_line": <int>, "max_lines": <int>} - Output is prefixed with the real 1-indexed line number of each line.\n    '
        '4. `read_symbol`: {"filepath": "<str>", "symbol_name": "<str>"} - Extracts a specific function, method, or class from a Python file.\n    '
        '5. `run_cmd`: {"command": "<str>"}'
    )

    format_example = r'<tool_call>{"name": "read_symbol", "args": {"filepath": "target.py", "symbol_name": "failing_function"}}</tool_call>'

    return f"""You are an elite, read-only coding consultant. Use tools modularly to answer questions about a codebase.

    AVAILABLE TOOLS:
    {tools_section}

    CRITICAL RULES:
    1. If the user's request requires reading or inspecting something you do NOT already have in this conversation, you MUST use the appropriate tool. Never guess or answer from memory.
    2. If the content was already retrieved earlier in this conversation, use it directly. Do NOT call the tool again for the same file or symbol.
    3. You are in READ-ONLY mode. Write and modification tools do not exist and must never be called.
    4. If the task is complete or you only need to talk to the user, DO NOT output a tool call. Reply in plain text.
    5. The JSON tool call MUST be minified on a SINGLE LINE.
    6. NEVER print, repeat, or summarize file contents in standard conversational text outside of a direct answer to the user's question.
    7. Only propose a corrected or modified version of code if the user has described a bug or explicitly asked for a change. Do not invent problems or rewrite code that wasn't reported as broken.
    8. Do exactly what was asked, then stop. Do not proactively fetch related files or symbols the user did not request.

    REQUIRED FORMAT EXAMPLE:
    {format_example}"""


def initialize_agent():
    """Initializes LLM dynamically according to registry config, GPU, and RAM."""
    global llm, SYSTEM_PROMPT, messages, CONTEXT_WINDOW

    if llm is not None:
        return

    if not os.path.exists(target_path):
        print(f"❌ Error: Model file not found at {target_path}")
        sys.exit(1)

    print(f"Loading {loaded_model_name}...")

    total_ram = get_system_ram_gb()

    max_ctx = active_config["max_context"]
    base_gpu_contexts = [32768, 16384, 8192]
    gpu_contexts = sorted(list(set([min(ctx, max_ctx) for ctx in base_gpu_contexts])), reverse=True)

    if total_ram >= 24:
        cpu_contexts = gpu_contexts
    elif total_ram >= 12:
        cpu_contexts = [ctx for ctx in gpu_contexts if ctx <= 16384]
    else:
        cpu_contexts = [ctx for ctx in gpu_contexts if ctx <= 8192]

    has_gpu = getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)()

    configured_layers = active_config["gpu_layers"]
    gpu_layer_attempts = configured_layers if isinstance(configured_layers, list) else [configured_layers]

    # --- 1. ATTEMPT GPU LOAD (full then partial offload) ---
    if has_gpu:
        for n_layers in gpu_layer_attempts:
            for ctx_size in gpu_contexts:
                try:
                    label = "full" if n_layers == -1 else f"partial ({n_layers} layers)"
                    print(f"🔄 Attempting GPU load [{label}] with {ctx_size} context...")
                    llm = Llama(
                        model_path=str(target_path),
                        n_ctx=ctx_size,
                        n_threads=6,
                        n_batch=512,
                        n_gpu_layers=n_layers,
                        chat_format=active_config["chat_format"],
                        flash_attn=True,
                        verbose=False
                    )
                    CONTEXT_WINDOW = ctx_size
                    print(f"🚀 Loaded on GPU [{label}] (Context: {CONTEXT_WINDOW}).")
                    break
                except Exception as e:
                    print(f"⚠️ GPU load failed [{label}] at {ctx_size} context: {e}")
            if llm is not None:
                break

    # --- 2. CPU FALLBACK ---
    if llm is None:
        print(f"🐢 Running on CPU (Detected System RAM: {total_ram:.1f} GB)...")
        for ctx_size in cpu_contexts:
            try:
                print(f"🔄 Attempting CPU load with {ctx_size} context...")
                llm = Llama(
                    model_path=str(target_path),
                    n_ctx=ctx_size,
                    n_threads=6,
                    n_batch=512,
                    n_gpu_layers=0,
                    chat_format=active_config["chat_format"],
                    verbose=False
                )
                CONTEXT_WINDOW = ctx_size
                print(f"🐢 Loaded on CPU (Context: {CONTEXT_WINDOW}).")
                break
            except Exception as e_cpu:
                print(f"⚠️ CPU allocation failed at {ctx_size} context: {e_cpu}")

    if llm is None:
        print("❌ Critical Error: Unable to initialize model on GPU or CPU.")
        sys.exit(1)

    SYSTEM_PROMPT = build_consultant_system_prompt()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]


def check_context_guardrail(current_messages, model, limit):
    """Calculates tokens and warns on memory overload."""
    try:
        tokens = sum(len(model.tokenize(m["content"].encode('utf-8'))) + 10 for m in current_messages)
        if tokens > limit:
            print(
                f"\n🚨 [MEMORY OVERLOAD]: Prompt size is {tokens} tokens (Limit: {limit}).\n   The consultant will likely hallucinate... Consider using '/clear'.")
        elif tokens > int(limit * 0.85):
            print(
                f"\n⚠️  [MEMORY WARNING]: Approaching context limit ({tokens}/{limit} tokens, {(tokens / limit) * 100:.1f}%).")
    except Exception:
        pass


def main():
    global messages, session_cwd, consult_read_cache

    initialize_agent()

    print(f"\n🔍 [Coding Consultant] {loaded_model_name} loaded. Read-only — write tools are disabled.\n")

    # --- Main Consultant Loop ---
    while True:
        user_input = get_user_prompt()

        if user_input == "/quit":
            print("Exiting. Goodbye!")
            sys.exit(0)

        if user_input == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            session_cwd = os.getcwd()
            consult_read_cache = {}
            print("🧹 Memory and environment completely cleared!")
            continue

        if user_input == "/cancel":
            print("❌ Current draft discarded.")
            continue

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # --- Per-turn state ---
        consecutive_errors = 0
        recent_tool_signatures = []
        real_tool_calls_this_turn = 0

        # Internal Consultant Execution Loop
        while True:
            check_context_guardrail(messages, llm, CONTEXT_WINDOW)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, messages)
                if interrupted:
                    break

                tool_request = payload_parser.extract_tool_call(response_content, allow_patch=False)

                if not tool_request:
                    # Plain text reply — nothing left to do this turn.
                    break

                tool_name = tool_request.get("name")
                tool_args = tool_request.get("args", {})

                # --- GUARDRAIL: write tools must never be reachable here ---
                if tool_name in WRITE_TOOLS:
                    print(f"🛑 [Consult Guardrail] Blocked disallowed tool `{tool_name}` in read-only mode.")
                    messages.append({
                        "role": "user",
                        "content": f"System Alert: Tool `{tool_name}` is strictly disabled in Consult Mode. "
                                   f"Do NOT attempt to execute it. Please output your suggested code fix "
                                   f"directly in a standard Markdown block instead."
                    })
                    continue

                if tool_name in ("read_file", "run_cmd") and "<payload>" in response_content:
                    messages.append({"role": "user",
                                     "content": f"System Alert: Tool `{tool_name}` does NOT accept <payload> blocks. Retry with ONLY the JSON block."})
                    continue

                # Path resolution
                for key in ["filepath", "dir_path"]:
                    if key in tool_args and not os.path.isabs(tool_args[key]):
                        tool_args[key] = os.path.abspath(os.path.join(session_cwd, tool_args[key]))

                # --- CACHE INTERCEPT for read_file / read_symbol ---
                if tool_name in ("read_file", "read_symbol"):
                    cache_key = (
                        tool_name,
                        tool_args.get("filepath"),
                        tool_args.get("symbol_name"),
                        tool_args.get("start_line"),
                        tool_args.get("max_lines"),
                    )
                    if cache_key in consult_read_cache:
                        print(f"📎 [Consult Cache] Already fetched this in-session — reusing cached result, no re-execution needed.")
                        cached_result = consult_read_cache[cache_key]

                        # Fed back in EXACTLY the same shape as a real tool execution result —
                        # not a special "note" — so the model treats it the same way it treats
                        # every other successful tool call it has seen this session.
                        messages.append(
                            {"role": "user", "content": f"Tool Execution Result:\n{cached_result}"})

                        curr_sig = f"{tool_name}:{str(tool_args)}"
                        recent_tool_signatures.append(curr_sig)
                        recent_tool_signatures = recent_tool_signatures[-6:]
                        if recent_tool_signatures.count(curr_sig) >= 3:
                            print("🛑 [Circuit Breaker] Repeated cache hits without progress. Forcing a text-only final answer.")
                            messages.append({
                                "role": "user",
                                "content": "Tool calls are disabled for this response. You already have "
                                           "the full content you need above. Answer the user's original "
                                           "question now, in plain text only — do not attempt a tool call."
                            })
                            response_content, _, _ = stream_agent_response(llm, messages)
                            break
                        continue

                # Loop guardrail (repeated, non-cached calls that aren't converging)
                curr_sig = f"{tool_name}:{str(tool_args)}"
                recent_tool_signatures.append(curr_sig)
                recent_tool_signatures = recent_tool_signatures[-6:]
                repeat_count = recent_tool_signatures.count(curr_sig)
                if repeat_count >= 2:
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        print("🛑 [Circuit Breaker] Consultant loop. Forcing turn end.")
                        break
                    messages.append({"role": "user",
                                     "content": f"System Alert: This exact tool call has been attempted {repeat_count} times "
                                                f"recently and is not succeeding. Do not repeat it verbatim — either fix the "
                                                f"underlying issue or try a different approach."})
                    continue

                # --- ONE REAL TOOL CALL PER USER TURN ---
                # Consult mode is for targeted, single-lookup investigation with a human
                # approving every step — not open-ended agentic chaining. Capping this stops
                # the model from proactively fetching unrequested context (e.g. adjacent
                # symbols) once it has already satisfied the question actually asked.
                if real_tool_calls_this_turn >= MAX_TOOL_CALLS_PER_TURN:
                    print("\n💬 [Consult] Tool call budget for this turn reached. Awaiting your next question.")
                    break

                # --- EXECUTION ---
                print(f"\n⚠️  CONSULTANT REQUESTS EXECUTION: {tool_name}")
                print(f"Arguments: {tool_args}")

                approval = input("Allow this action? (y/n/edit): ").strip().lower()
                tool_result, tool_reinforcement = "", ""

                if approval == 'y':
                    tool_result, tool_reinforcement, _was_mod = execute_tool(tool_name, tool_args, is_split_mode=False)
                    real_tool_calls_this_turn += 1
                    print(f"⚙️  Tool execution finished.")

                    MAX_RESULT_PREVIEW = 400
                    if len(tool_result) > MAX_RESULT_PREVIEW:
                        preview = tool_result[:MAX_RESULT_PREVIEW]
                        print(f"   Result ({len(tool_result)} chars, truncated): {preview}...")
                    else:
                        print(f"   Result: {tool_result}")

                    if tool_name in ("read_file", "read_symbol"):
                        cache_key = (
                            tool_name,
                            tool_args.get("filepath"),
                            tool_args.get("symbol_name"),
                            tool_args.get("start_line"),
                            tool_args.get("max_lines"),
                        )
                        consult_read_cache[cache_key] = tool_result

                elif approval == 'edit':
                    tool_result = f"User denied and provided feedback: {input('Feedback: ')}"
                else:
                    tool_result = "User denied permission."
                    print("🛑 Action blocked.")

                messages.append(
                    {"role": "user", "content": f"Tool Execution Result:\n{tool_result}{tool_reinforcement}"})

                if approval == 'y':
                    print("\n💬 [Consult] Tool call complete. Awaiting your next question (mode stays read-only).")
                    break

            except json.JSONDecodeError as e:
                print(f"\n❌ [Parser Interceptor] Halted syntax loop.")
                messages.append({"role": "user",
                                 "content": f"Formatting Failure: {e}\nRemember to use raw unescaped content, no extra wrapping."})
                break
            except Exception as e:
                print(f"\n[Error during generation]: {e}")
                break


if __name__ == "__main__":
    main()
