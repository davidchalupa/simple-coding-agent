import sys
import os
import json
import psutil
import re

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
# ToDo: make this tunable
MAX_TOOL_CALLS_PER_TURN = 20


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
    4. If the task is complete, reply in plain text. You MAY output multiple `<tool_call>` blocks in a single response to fetch information in parallel (e.g., reading multiple files).
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
                        n_batch=256,
                        n_gpu_layers=n_layers,
                        chat_format=active_config["chat_format"],
                        flash_attn=False,
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


def fuzzy_extract_tool_calls(text):
    """
    Hunts for any valid JSON object containing 'name' and 'args',
    regardless of how the LLM wrapped, separated, or formatted them.
    """
    tools = []
    # Find every starting position of what looks like a tool call
    start_indices = [m.start() for m in re.finditer(r'\{\s*"name"\s*:', text)]

    for start_idx in start_indices:
        brace_count = 0
        in_string = False
        escape = False

        for i in range(start_idx, len(text)):
            char = text[i]

            if escape:
                escape = False
                continue

            if char == '\\':
                escape = True
            elif char == '"':
                in_string = not in_string
            elif not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1

                    if brace_count == 0:
                        # Reached the end of this specific JSON object
                        json_str = text[start_idx:i + 1]
                        try:
                            parsed = json.loads(json_str)
                            if "name" in parsed:
                                tools.append(parsed)
                        except json.JSONDecodeError:
                            pass
                        break
    return tools


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
        real_tool_calls_this_turn = 0

        # Internal Consultant Execution Loop
        while True:
            check_context_guardrail(messages, llm, CONTEXT_WINDOW)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, messages)
                if interrupted:
                    break

                # --- 1. Regex extraction for <tool_call> tags ---
                raw_calls = re.findall(r'<tool_call>(.*?)</tool_call>', response_content, re.DOTALL)
                tool_requests = []

                if raw_calls:
                    for call in raw_calls:
                        try:
                            parsed = json.loads(call)
                            if "name" in parsed:
                                tool_requests.append(parsed)
                        except json.JSONDecodeError:
                            continue
                else:
                    # --- 2. Fallback: Check if the model output a JSON array instead ---
                    try:
                        clean_content = response_content
                        if "```json" in clean_content:
                            clean_content = clean_content.split("```json")[1].split("```")[0]
                        elif "```" in clean_content:
                            clean_content = clean_content.split("```")[1].split("```")[0]

                        clean_content = clean_content.strip()
                        if clean_content.startswith("[") and clean_content.endswith("]"):
                            parsed_array = json.loads(clean_content)
                            if isinstance(parsed_array, list):
                                for item in parsed_array:
                                    if isinstance(item, dict) and "name" in item:
                                        tool_requests.append(item)
                    except Exception:
                        pass

                    # --- 3. Ultimate Fallback: Fuzzy brace-counting extraction ---
                    if not tool_requests:
                        tool_requests = fuzzy_extract_tool_calls(response_content)

                if not tool_requests:
                    # Plain text reply — nothing left to do this turn.
                    print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                    break

                combined_results = ""

                # --- PROCESS all extracted tools sequentially ---
                for tool_request in tool_requests:
                    tool_name = tool_request.get("name")
                    tool_args = tool_request.get("args", {})

                    if tool_name in WRITE_TOOLS:
                        print(f"🛑 [Consult Guardrail] Blocked disallowed tool `{tool_name}`.")
                        combined_results += f"System Alert: Tool `{tool_name}` is strictly disabled.\n\n"
                        continue

                    if tool_name in ("read_file", "run_cmd") and "<payload>" in response_content:
                        combined_results += f"System Alert: `{tool_name}` does NOT accept <payload> blocks.\n\n"
                        continue

                    for key in ["filepath", "dir_path"]:
                        if key in tool_args and not os.path.isabs(tool_args[key]):
                            tool_args[key] = os.path.abspath(os.path.join(session_cwd, tool_args[key]))

                    # --- CACHE INTERCEPT ---
                    cache_key = None
                    if tool_name in ("read_file", "read_symbol"):
                        cache_key = (
                            tool_name, tool_args.get("filepath"), tool_args.get("symbol_name"),
                            tool_args.get("start_line"), tool_args.get("max_lines")
                        )
                        if cache_key in consult_read_cache:
                            print(
                                f"📎 [Consult Cache] Reusing cached result for {tool_name} on {tool_args.get('filepath')}.")
                            combined_results += f"Result for {tool_name}:\n{consult_read_cache[cache_key]}\n\n"
                            continue

                    # --- MAX CALLS CHECK ---
                    if real_tool_calls_this_turn >= MAX_TOOL_CALLS_PER_TURN:
                        print("\n💬 [Consult] Tool call budget reached. Skipping remaining queued tools.")
                        break

                    # --- EXECUTION ---
                    print(f"\n⚠️  CONSULTANT REQUESTS EXECUTION: {tool_name}")
                    print(f"Arguments: {tool_args}")

                    approval = input("Allow this action? (y/n/edit): ").strip().lower()

                    if approval == 'y':
                        tool_result, tool_reinforcement, _ = execute_tool(tool_name, tool_args, is_split_mode=False)
                        real_tool_calls_this_turn += 1
                        print(f"⚙️  Tool execution finished.")

                        MAX_RESULT_PREVIEW = 400
                        if len(tool_result) > MAX_RESULT_PREVIEW:
                            print(
                                f"   Result ({len(tool_result)} chars, truncated): {tool_result[:MAX_RESULT_PREVIEW]}...")
                        else:
                            print(f"   Result: {tool_result}")

                        if cache_key:
                            consult_read_cache[cache_key] = tool_result

                        combined_results += f"Result for {tool_name}:\n{tool_result}{tool_reinforcement}\n\n"

                    elif approval == 'edit':
                        feedback = input('Feedback: ')
                        combined_results += f"User denied {tool_name}. Feedback: {feedback}\n\n"
                    else:
                        print("🛑 Action blocked.")
                        combined_results += f"User denied permission for {tool_name}.\n\n"

                # --- FEEDBACK & LOOP CONTINUATION ---
                if combined_results.strip():
                    messages.append(
                        {"role": "user", "content": f"Tool Execution Results:\n{combined_results.strip()}"})

                # If we hit the budget, add a forceful prompt to make the model stop tooling and answer
                if real_tool_calls_this_turn >= MAX_TOOL_CALLS_PER_TURN:
                    messages.append({
                        "role": "user",
                        "content": "Tool limit reached for this turn. Provide your final answer in plain text based on the retrieved context."
                    })

                # The loop cycles back up to stream_agent_response() here
                # so the LLM can generate text (or more tool calls) based on the combined_results!

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
