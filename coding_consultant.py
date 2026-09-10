import sys
import os
import gc
import json
import re
import time
import subprocess
import tempfile

from pathlib import Path

from llama_cpp import llama_cpp as llama_backend

from common.llm_init import LLMInitializer

from common.input_handler import get_user_prompt
from common.execute_tool import execute_tool
from common.output_handler import stream_agent_response
from common.guardrail_tools import check_context_guardrail

from consultant.system_prompt_builder import build_consultant_system_prompt
from consultant.guardrail_tools import fuzzy_extract_tool_calls

from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY


# --- Sanitization for models that emit reasoning traces / chat-template leakage ---
# (e.g. DeepSeek-R1-Distill-Qwen). No-op for models that never produce these,
# such as Qwen2.5-Coder, so behavior there is unchanged.
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
STRAY_TOKEN_RE = re.compile(r"<\|im_start\|>\s*assistant\s*|<\|im_end\|>|<\|im_start\|>")

MAX_STATE2_VIOLATIONS_PER_TURN = 3
DIAGNOSE_RE = re.compile(r'^/diagnose\b\s*(.*)', re.IGNORECASE | re.DOTALL)
MAX_DIAGNOSE_GATHER_ROUNDS = 6

# Must match the tool names actually defined in build_consultant_system_prompt().
# Anything outside this set is a hallucinated tool (e.g. "load_file") and gets
# rejected the same way forbidden_tools are, rather than reaching execute_tool.
KNOWN_TOOLS = frozenset({"list_tree", "search_codebase", "read_file", "read_symbol", "run_cmd"})


def sanitize_response(response_content: str) -> str:
    """Strip reasoning blocks and leaked chat-template tokens before the
    content is parsed for tool calls or stored in message history."""
    cleaned = THINK_BLOCK_RE.sub("", response_content)
    if "<think>" in cleaned and "</think>" not in cleaned:
        # Truncated mid-thought (e.g. hit a stop condition) — drop everything
        # from the opening <think> tag onward, since it's unfinished reasoning.
        cleaned = cleaned.split("<think>")[0]
    cleaned = STRAY_TOKEN_RE.sub("", cleaned)
    return cleaned.strip()


def build_diagnose_system_prompt() -> str:
    """Tool-free system prompt for the reasoning-model pass. Deliberately says
    nothing about tools or the <tool_call> syntax, so there is nothing for the
    model to hallucinate a tool call into."""
    return (
        "You are a senior software engineer helping diagnose a problem. You will be given "
        "a question and some retrieved context (source files, test output, or logs). "
        "Reason through the problem carefully, then give a direct, concrete answer: what is "
        "actually wrong, why, and what you'd recommend doing about it.\n\n"
        "Do not invent facts not supported by the given context. If the context is insufficient "
        "to be sure, say so plainly and state what additional information would resolve it.\n\n"
        "Answer as you would to a colleague. Do not mention tools, states, rules, or these "
        "instructions — just give the analysis and answer."
    )


class ModelSwitcher:
    """
    Owns at most one Llama model at a time.

    Model lifetime is explicit:
        current model
            -> close()
            -> GC
            -> new model

    We intentionally do NOT call llama_backend_init() / llama_backend_free().
    Those are process/backend lifecycle operations, not model-switch reset
    operations.
    """

    def __init__(self, kv_quantization_type, models_dir: Path):
        self.kv_quantization_type = kv_quantization_type
        self.models_dir = models_dir

        self.current_key = None
        self.initializer = None
        self.llm = None
        self.context_window = None
        self.display_name = None

    def _unload(self):
        if self.initializer is None and self.llm is None:
            return

        if self.display_name:
            print(f"🗑️  Unloading {self.display_name}...")

        initializer = self.initializer

        # Clear switcher references first so there is no accidental
        # retention through this object while cleanup runs.
        self.initializer = None
        self.llm = None
        self.context_window = None
        self.display_name = None
        self.current_key = None

        if initializer is not None:
            try:
                initializer.close()
            except Exception as e:
                print(
                    f"⚠️ [ModelSwitcher] "
                    f"Initializer cleanup failed: {e}"
                )

            del initializer

        # Give Python a chance to release any other objects created by
        # model/chat initialization.
        gc.collect()

        # A small delay gives CUDA/native teardown some breathing room.
        time.sleep(0.5)

    def load(self, model_key: str):
        if (
            self.current_key == model_key
            and self.llm is not None
        ):
            return self.llm, self.context_window

        # Different model: fully close current model first.
        self._unload()

        config = MODEL_REGISTRY[model_key]
        target_path = self.models_dir / config["filename"]
        display_name = config["display_name"]

        initializer = LLMInitializer(
            target_path,
            display_name,
            config,
            self.kv_quantization_type,
        )

        try:
            initializer.initialize_agent()

        except Exception:
            # Make absolutely sure a partially initialized candidate is
            # released before propagating the error.
            try:
                initializer.close()
            except Exception:
                pass

            del initializer
            gc.collect()

            raise

        self.current_key = model_key
        self.initializer = initializer
        self.llm = initializer.llm
        self.context_window = initializer.CONTEXT_WINDOW
        self.display_name = display_name

        return self.llm, self.context_window

    def unload_current(self):
        """
        Fully releases the current model before another process is started,
        e.g. the /diagnose reasoning worker.
        """
        self._unload()


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

        # Tracks whether we're in STATE 2 (plain-text-only) of the state machine.
        # Enforced in code rather than relying solely on the prompt, since some
        # models (e.g. DeepSeek-R1-Distill) don't reliably self-enforce this.
        self.expect_plain_text = False
        self.state2_violations = 0
        self.last_directive = None  # the specific STATE 2 directive currently in force


parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
state = ConsultantState(parsed_args)


def is_pure_load_request(user_input: str) -> bool:
    """Checks if the user prompt is strictly requesting to load/read context without asking a task."""
    lowered = user_input.strip().lower()
    load_keywords = ("load ", "read ", "bring ", "fetch ", "show ")
    task_keywords = ("how", "why", "change", "refactor", "modify", "add", "fix", "update", "draft", "write", "create",
                     "implement", "start")

    is_load_cmd = any(lowered.startswith(kw) or f" {kw}" in lowered for kw in load_keywords) or lowered.endswith(
        "into context")
    has_task_cmd = any(kw in lowered for kw in task_keywords) or "?" in lowered

    return is_load_cmd and not has_task_cmd


def handle_user_input(state, user_input, system_prompt):
    if user_input == "/quit":
        print("Exiting. Goodbye!")
        sys.exit(0)

    if user_input == "/clear":
        state.messages = [{"role": "system", "content": system_prompt}]
        state.session_cwd = os.getcwd()
        state.consult_read_cache = {}
        state.expect_plain_text = False
        state.state2_violations = 0
        state.last_directive = None
        print("🧹 Memory and environment completely cleared!")
        return True

    if user_input == "/cancel":
        print("❌ Current draft discarded.")
        return True

    if not user_input:
        return True

    return False


def extract_tool_requests(response_content):
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

        if not tool_requests:
            tool_requests = fuzzy_extract_tool_calls(response_content)

    return tool_requests


def process_tool_requests(state, response_content, tool_requests, real_tool_calls_this_turn, compact_cache_hits=False):
    combined_results = ""

    for tool_request in tool_requests:
        tool_name = tool_request.get("name")
        tool_args = tool_request.get("args", {})

        if tool_name in state.forbidden_tools:
            print(f"🛑 [Consult Guardrail] Blocked disallowed tool `{tool_name}`.")
            combined_results += f"System Alert: Tool `{tool_name}` is strictly disabled.\n\n"
            continue

        if tool_name not in KNOWN_TOOLS:
            print(f"🛑 [Consult Guardrail] Blocked unknown tool `{tool_name}` (not a real tool).")
            combined_results += (
                f"System Alert: `{tool_name}` is not a valid tool. "
                f"Valid tools are: {', '.join(sorted(KNOWN_TOOLS))}.\n\n"
            )
            continue

        if tool_name in ("read_file", "run_cmd") and "<payload>" in response_content:
            combined_results += f"System Alert: `{tool_name}` does NOT accept <payload> blocks.\n\n"
            continue

        for key in ["filepath", "dir_path"]:
            if key in tool_args and not os.path.isabs(tool_args[key]):
                tool_args[key] = os.path.abspath(os.path.join(state.session_cwd, tool_args[key]))

        cache_key = None
        if tool_name in ("read_file", "read_symbol"):
            cache_key = (
                tool_name, tool_args.get("filepath"), tool_args.get("symbol_name"),
                tool_args.get("start_line"), tool_args.get("max_lines")
            )
            if cache_key in state.consult_read_cache:
                if compact_cache_hits:
                    print(f"📎 [Consult Cache] {tool_name} on {tool_args.get('filepath')} already available "
                          f"— not re-pasting into the gather transcript (it's already in context and will be "
                          f"included in the analysis automatically).")
                    combined_results += (
                        f"Note: {tool_name} result for {tool_args.get('filepath')} is already available from "
                        f"earlier in this session and will automatically be included in the analysis. "
                        f"No need to request it again.\n\n"
                    )
                else:
                    print(
                        f"📎 [Consult Cache] Reusing cached result for {tool_name} on {tool_args.get('filepath')}.")
                    combined_results += f"Result for {tool_name}:\n[Note: Content already loaded in context history]\n{state.consult_read_cache[cache_key]}\n\n"
                continue

        if real_tool_calls_this_turn >= state.max_tool_calls_per_turn:
            print("\n💬 [Consult] Tool call budget reached. Skipping remaining queued tools.")
            break

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
                state.consult_read_cache[cache_key] = tool_result

            combined_results += f"Result for {tool_name}:\n{tool_result}{tool_reinforcement}\n\n"

        elif approval == 'edit':
            feedback = input('Feedback: ')
            combined_results += f"User denied {tool_name}. Feedback: {feedback}\n\n"
        else:
            print("🛑 Action blocked.")
            combined_results += f"User denied permission for {tool_name}.\n\n"

    return combined_results, real_tool_calls_this_turn


def build_cached_context_summary(state):
    """Formats everything already read this session (state.consult_read_cache)
    as context text. Used so /diagnose can see files loaded in *earlier* turns
    even when this turn's gathering pass makes no fresh tool calls (because
    the gathering model correctly recognizes it's all already cached)."""
    if not state.consult_read_cache:
        return ""
    parts = []
    for cache_key, result in state.consult_read_cache.items():
        tool_name, filepath = cache_key[0], cache_key[1]
        label = filepath or "(unknown path)"
        parts.append(f"Cached {tool_name} result for {label}:\n{result}")
    return "\n\n".join(parts)


def gather_context_for_diagnose(state, switcher, user_question):
    """Runs the STATE 1 tool-calling loop on the primary (gathering) model
    only, never letting it produce a final natural-language answer. Returns
    the concatenated text of everything retrieved via tools this turn.
    Reuses the same extraction/execution/guardrail machinery as the normal
    flow, so cache reuse, forbidden-tool blocking, and unknown-tool blocking
    all behave identically here.
    """
    llm, context_window = switcher.load(state.primary_model_key)

    gathered_text = ""
    real_tool_calls_this_turn = 0

    # Ask the gathering model to load whatever it needs, but make clear this
    # turn's job is retrieval only — a second (reasoning) pass will answer.
    state.messages.append({
        "role": "user",
        "content": (
            f"{user_question}\n\n"
            "[SYSTEM DIRECTIVE: If the files or context you need are already visible above in this "
            "conversation, use them as-is — do not re-list directories or re-read files to double-check. "
            "Only call tools for things that are genuinely missing. Use tool calls to load any such "
            "missing files or context needed to answer this. Do not answer the question yourself — once "
            "you have what you need, or if nothing further is needed, simply stop issuing tool calls.]"
        )
    })

    stagnant_rounds = 0
    seen_call_signatures = set()
    for round_num in range(MAX_DIAGNOSE_GATHER_ROUNDS):
        check_context_guardrail(state.messages, llm, context_window)

        response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages)
        if interrupted:
            break

        response_content = sanitize_response(response_content)
        state.messages.append({"role": "assistant", "content": response_content})

        tool_requests = extract_tool_requests(response_content)
        if not tool_requests:
            break

        perfect_history = "\n".join(
            [f'<tool_call>{json.dumps(req)}</tool_call>' for req in tool_requests])
        state.messages.append({"role": "assistant", "content": perfect_history})

        combined_results, real_tool_calls_this_turn = process_tool_requests(
            state, response_content, tool_requests, real_tool_calls_this_turn, compact_cache_hits=True)

        # Detect a round that only repeats calls (same name + args) already
        # made earlier this gather turn — that's the model looping rather
        # than making progress (e.g. re-asking for a call that was already
        # denied), regardless of whether earlier rounds were cache hits.
        call_signatures = [
            json.dumps({"name": r.get("name"), "args": r.get("args", {})}, sort_keys=True)
            for r in tool_requests
        ]
        is_repeat_round = all(sig in seen_call_signatures for sig in call_signatures)
        seen_call_signatures.update(call_signatures)
        stagnant_rounds = stagnant_rounds + 1 if is_repeat_round else 0

        if combined_results.strip():
            gathered_text += combined_results
            remaining = MAX_DIAGNOSE_GATHER_ROUNDS - round_num - 1
            state.messages.append({
                "role": "user",
                "content": f"Tool Execution Results:\n{combined_results.strip()}\n\n"
                           f"[SYSTEM DIRECTIVE: Continue gathering only if something is still genuinely "
                           f"missing, or stop if you have enough. {remaining} gathering round(s) remain.]"
            })

        if stagnant_rounds >= 2:
            print("\n💬 [Consult] Gathering is repeating the same call(s) with no new progress — stopping early.")
            break

        if real_tool_calls_this_turn >= state.max_tool_calls_per_turn:
            print("\n💬 [Consult] Tool call budget reached during gathering.")
            break

    return gathered_text


REASONING_WORKER_PATH = Path(__file__).resolve().parent / "consultant_reasoning_worker.py"


def run_reasoning_subprocess(
    model_key,
    kv_quantization_type,
    models_dir,
    system_prompt,
    user_content,
):
    """
    Run one reasoning completion in a fresh process.

    The child process owns the reasoning model for its entire lifetime.
    When it exits, the OS releases all of its native/CUDA resources.
    """

    fd, result_path = tempfile.mkstemp(
        prefix="consult_diagnose_",
        suffix=".json",
    )
    os.close(fd)

    payload = json.dumps({
        "model_key": model_key,
        "kv_quantization_type": kv_quantization_type,
        "models_dir": str(models_dir),
        "system_prompt": system_prompt,
        "user_content": user_content,
    })

    try:
        completed = subprocess.run(
            [sys.executable, str(REASONING_WORKER_PATH), result_path],
            input=payload,
            text=True,
            cwd=str(Path(__file__).resolve().parent),
            check=False,
        )

        if completed.returncode != 0:
            # The worker should normally write an error result, but if it
            # crashed before doing so, make that explicit.
            if not os.path.exists(result_path):
                return (
                    None,
                    f"Reasoning worker exited with code "
                    f"{completed.returncode} without producing a result."
                )

        try:
            with open(result_path, "r") as f:
                result = json.load(f)

        except Exception as e:
            return (
                None,
                f"Worker produced no readable result ({e}); "
                f"exit code={completed.returncode}."
            )

        content = result.get("content")
        error = result.get("error")

        if completed.returncode != 0 and not error:
            error = (
                f"Reasoning worker exited with code "
                f"{completed.returncode}."
            )

        return content, error

    finally:
        try:
            os.remove(result_path)
        except OSError:
            pass


def run_diagnose_turn(state, switcher, user_question):
    """Gathers context with the primary (tool-calling) model in-process,
    then runs the reasoning model in an isolated subprocess for the
    tool-free analysis pass. The primary model is never unloaded for this —
    only the reasoning model's process starts and exits per call."""
    gathered_text = gather_context_for_diagnose(state, switcher, user_question)

    analysis_system = build_diagnose_system_prompt()
    cached_summary = build_cached_context_summary(state)
    combined_context = "\n\n".join(part for part in [gathered_text.strip(), cached_summary] if part.strip())
    context_block = combined_context if combined_context.strip() else \
        "(No context has been loaded yet this session — nothing to analyze.)"
    analysis_user_content = f"Question:\n{user_question}\n\nRetrieved context:\n{context_block}"

    if state.reasoning_model_key is None:
        print("⚠️  No --reasoning-model configured; running /diagnose with the primary model only.")
        llm, context_window = switcher.load(state.primary_model_key)
        analysis_messages = [
            {"role": "system", "content": analysis_system},
            {"role": "user", "content": analysis_user_content}
        ]
        check_context_guardrail(analysis_messages, llm, context_window)
        response_content, is_truncated, interrupted = stream_agent_response(llm, analysis_messages)
        final_answer = sanitize_response(response_content) if not interrupted else None
        error = "Analysis was interrupted." if interrupted else None
    else:
        print(f"\n🗑️  [Consult] Releasing GPU for the reasoning worker — your hardware fits one ~7B "
              f"model in VRAM at a time, so {state.primary_model_key} needs to step aside temporarily...")
        switcher.unload_current()
        try:
            print(f"\n🧠 [Consult] Running reasoning model in an isolated process...")
            final_answer, error = run_reasoning_subprocess(
                state.reasoning_model_key, state.kv_quantization_type, state.models_dir,
                analysis_system, analysis_user_content
            )
        finally:
            print(f"\n🔁 [Consult] Reloading {state.primary_model_key}...")
            switcher.load(state.primary_model_key)

    if error:
        print(f"\n⚠️  [Consult] Diagnose failed: {error}")
    elif final_answer:
        print(f"\n[Consultant]: {final_answer}")
        state.messages.append({"role": "assistant", "content": final_answer})
    else:
        print("\n⚠️  [Consult] Diagnose produced no answer.")

    state.expect_plain_text = False
    state.state2_violations = 0
    state.last_directive = None


def main(state):
    system_prompt = build_consultant_system_prompt()
    switcher = ModelSwitcher(state.kv_quantization_type, state.models_dir)
    switcher.load(state.primary_model_key)

    state.messages = [{"role": "system", "content": system_prompt}]

    print(f"\n🔍 [Coding Consultant] {switcher.display_name} loaded. Read-only — write tools are disabled.")
    if state.reasoning_model_key is not None:
        reasoning_display = MODEL_REGISTRY[state.reasoning_model_key]["display_name"]
        print(f"🧠 Diagnose mode available: prefix a question with '/diagnose' to reason "
              f"with {reasoning_display} over gathered context.")
    print()

    while True:
        user_input = get_user_prompt()

        if handle_user_input(state, user_input, system_prompt):
            continue

        diagnose_match = DIAGNOSE_RE.match(user_input)
        if diagnose_match:
            question = diagnose_match.group(1).strip()
            if not question:
                print("⚠️  Usage: /diagnose <your question> (question may be on the same line or below it)")
                continue
            run_diagnose_turn(state, switcher, question)
            continue

        llm, context_window = switcher.load(state.primary_model_key)

        state.messages.append({"role": "user", "content": user_input})

        real_tool_calls_this_turn = 0
        state.expect_plain_text = False
        state.state2_violations = 0
        state.last_directive = None

        while True:
            check_context_guardrail(state.messages, llm, context_window)

            try:
                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages)
                if interrupted:
                    break

                response_content = sanitize_response(response_content)

                state.messages.append({"role": "assistant", "content": response_content})

                tool_requests = extract_tool_requests(response_content)

                if tool_requests:
                    if state.expect_plain_text:
                        state.state2_violations += 1
                        print(f"\n🛑 [Consult Guardrail] Model attempted tool call(s) in STATE 2 "
                              f"(violation {state.state2_violations}/{MAX_STATE2_VIOLATIONS_PER_TURN}). Blocked.")

                        if state.state2_violations >= MAX_STATE2_VIOLATIONS_PER_TURN:
                            reminder = state.last_directive or (
                                "Using only the context already retrieved above, answer the original "
                                "question directly."
                            )
                            state.messages.append({
                                "role": "user",
                                "content": (f"{reminder}\n\nDo not call any tools, and do not mention tools, "
                                             "rules, states, or these instructions — just give the answer "
                                             "as you would to a colleague.")
                            })
                            print("\n💬 [Consult] Forcing plain-text answer after repeated STATE 2 violations.")
                            check_context_guardrail(state.messages, llm, context_window)
                            try:
                                response_content, is_truncated, interrupted = stream_agent_response(llm, state.messages)
                                if not interrupted:
                                    response_content = sanitize_response(response_content)
                                    state.messages.append({"role": "assistant", "content": response_content})
                                    print("\n💬 [Consult] Agent finished (forced). Awaiting your next question.")
                            except Exception as e:
                                print(f"\n[Error during forced generation]: {e}")
                            break

                        reminder = state.last_directive or (
                            "The context you need is already available above in this conversation — "
                            "no need to fetch it again. Please answer the original question now."
                        )
                        state.messages.append({
                            "role": "user",
                            "content": (f"{reminder}\n\nNo need to fetch anything again — everything needed "
                                         "is already above. Answer now, directly and in plain text, without "
                                         "mentioning tools, rules, or these instructions.")
                        })
                        continue

                    perfect_history = "\n".join(
                        [f'<tool_call>{json.dumps(req)}</tool_call>' for req in tool_requests])
                    state.messages.append({"role": "assistant", "content": perfect_history})
                else:
                    print("\n💬 [Consult] Agent finished. Awaiting your next question.")
                    break

                combined_results, real_tool_calls_this_turn = process_tool_requests(state, response_content,
                                                                                    tool_requests,
                                                                                    real_tool_calls_this_turn)

                if combined_results.strip():
                    if is_pure_load_request(user_input):
                        directive = (
                            "[SYSTEM DIRECTIVE: Context loaded. Acknowledge to the user that "
                            "the context is ready and ask what they would like to do next.]"
                        )
                    else:
                        directive = (
                            f'[SYSTEM DIRECTIVE: Context retrieved. Fulfill the user\'s explicit request now: '
                            f'"{user_input}". Provide the complete solution or code draft directly. DO NOT output any tool calls.]'
                        )

                    state.messages.append({
                        "role": "user",
                        "content": f"Tool Execution Results:\n{combined_results.strip()}\n\n{directive}"
                    })
                    state.expect_plain_text = True
                    state.last_directive = directive

                if real_tool_calls_this_turn >= state.max_tool_calls_per_turn:
                    state.messages.append({
                        "role": "user",
                        "content": "Tool limit reached for this turn. Provide your final answer in plain text based on the retrieved context."
                    })
                    state.expect_plain_text = True

            except json.JSONDecodeError as e:
                print(f"\n❌ [Parser Interceptor] Halted syntax loop.")
                state.messages.append({"role": "user",
                                       "content": f"Formatting Failure: {e}\nRemember to use raw unescaped content, no extra wrapping."})
                break
            except Exception as e:
                print(f"\n[Error during generation]: {e}")
                break


if __name__ == "__main__":
    main(state)
