import sys
import os
import json
import subprocess
import tempfile

from pathlib import Path

from common.output_handler import stream_agent_response
from common.guardrail_tools import check_context_guardrail

from consultant.system_prompt_builder import build_diagnose_system_prompt
from consultant.guardrail_tools import sanitize_response, build_trimmed_evidence_block
from consultant.tool_parser import extract_tool_requests
from consultant.tool_processor import process_tool_requests


MAX_DIAGNOSE_GATHER_ROUNDS = 6

def get_cached_context_entries(state):
    """Returns everything read this session (state.consult_read_cache) as
    structured entries, most-recently-loaded first. Used so /diagnose can see
    files loaded in *earlier* turns even when this turn's gathering pass
    makes no fresh tool calls, and so the context-budget trimming step can
    prioritize recent entries and drop whole older ones if space runs out."""
    entries = []
    for cache_key, result in state.consult_read_cache.items():
        tool_name, filepath = cache_key[0], cache_key[1]
        entries.append({
            "tool_name": tool_name,
            "label": filepath or "(unknown path)",
            "content": result,
        })
    # dict preserves insertion order (Python 3.7+); reverse for most-recent-first
    return list(reversed(entries))


def gather_context_for_diagnose(state, switcher, user_question):
    """
    Run a silent tool-gathering pass with the primary model.

    The primary model is used only to retrieve missing context. Its natural-
    language answers are deliberately discarded. Only tool calls and tool
    results participate in the gathering conversation.

    The gathering conversation is local to this /diagnose invocation, so the
    primary model's temporary retrieval instructions/responses do not pollute
    the consultant's persistent user-facing message history.
    """
    llm, context_window = switcher.load(state.primary_model_key)

    gathered_text = ""
    real_tool_calls_this_turn = 0

    # Work on a private conversation. Do NOT mutate state.messages while
    # gathering; otherwise Qwen's temporary retrieval conversation becomes
    # part of the consultant's persistent session history.
    gather_messages = list(state.messages)

    gather_messages.append({
        "role": "user",
        "content": (
            f"{user_question}\n\n"
            "[SYSTEM DIRECTIVE: This is a retrieval-only phase. "
            "Do not answer the user's question. Do not provide a diagnosis, "
            "recommendation, explanation, or summary. "
            "Inspect the conversation context and call tools only when "
            "additional source files, symbols, or test output are genuinely "
            "needed to answer the question. "
            "Once enough evidence has been retrieved, stop. "
            "Your ordinary natural-language response will be discarded.]"
        )
    })

    stagnant_rounds = 0
    seen_call_signatures = set()

    for round_num in range(MAX_DIAGNOSE_GATHER_ROUNDS):
        check_context_guardrail(
            gather_messages,
            llm,
            context_window,
        )

        # Qwen's output is intentionally silent during the gather phase.
        # stream_agent_response() writes streamed model output directly to
        # stdout; redirect it because Qwen is not the answering model here.
        import contextlib
        import io

        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()

        with contextlib.redirect_stdout(captured_stdout), \
             contextlib.redirect_stderr(captured_stderr):

            response_content, is_truncated, interrupted = (
                stream_agent_response(
                    llm,
                    gather_messages,
                )
            )

        if interrupted:
            break

        response_content = sanitize_response(response_content)

        tool_requests = extract_tool_requests(response_content)

        # IMPORTANT:
        # If Qwen answers in natural language with no tool calls, discard it.
        # This is precisely the behavior that previously leaked a Qwen answer
        # to the user before DeepSeek was started.
        if not tool_requests:
            break

        perfect_history = "\n".join(
            f'<tool_call>{json.dumps(req)}</tool_call>'
            for req in tool_requests
        )

        # Keep tool-call structure inside the PRIVATE gathering conversation.
        gather_messages.append({
            "role": "assistant",
            "content": perfect_history,
        })

        combined_results, real_tool_calls_this_turn = process_tool_requests(
            state,
            response_content,
            tool_requests,
            real_tool_calls_this_turn,
            compact_cache_hits=True,
        )

        call_signatures = [
            json.dumps(
                {
                    "name": r.get("name"),
                    "args": r.get("args", {}),
                },
                sort_keys=True,
            )
            for r in tool_requests
        ]

        is_repeat_round = all(
            sig in seen_call_signatures
            for sig in call_signatures
        )

        seen_call_signatures.update(call_signatures)

        stagnant_rounds = (
            stagnant_rounds + 1
            if is_repeat_round
            else 0
        )

        if combined_results.strip():
            gathered_text += combined_results

            remaining = MAX_DIAGNOSE_GATHER_ROUNDS - round_num - 1

            gather_messages.append({
                "role": "user",
                "content": (
                    f"Tool Execution Results:\n"
                    f"{combined_results.strip()}\n\n"
                    "[SYSTEM DIRECTIVE: Continue gathering only if "
                    "something is genuinely missing. Do not answer the "
                    "user's question. Stop when sufficient evidence has "
                    f"been collected. {remaining} gathering round(s) remain.]"
                ),
            })

        if stagnant_rounds >= 2:
            print(
                "\n💬 [Consult] Gathering is repeating the same "
                "call(s) with no new progress — stopping early."
            )
            break

        if real_tool_calls_this_turn >= state.max_tool_calls_per_turn:
            print(
                "\n💬 [Consult] Tool call budget reached during gathering."
            )
            break

    return gathered_text


REASONING_WORKER_PATH = Path(__file__).resolve().parent / "consultant_reasoning_worker.py"


def run_reasoning_subprocess(
    model_key,
    kv_quantization_type,
    models_dir,
    system_prompt,
    question,
    gathered_text,
    cached_entries,
):
    """
    Run one reasoning completion in a fresh process.

    The child process owns the reasoning model for its entire lifetime.
    When it exits, the OS releases all of its native/CUDA resources.

    Note: question/gathered_text/cached_entries are passed separately rather
    than as one pre-assembled prompt string, because the context-budget
    trimming (build_trimmed_evidence_block) can only be done correctly AFTER
    the model is loaded in the worker process — that's the only place the
    real context_window and a working tokenizer are available. The worker
    assembles and trims the final prompt itself.
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
        "question": question,
        "gathered_text": gathered_text,
        "cached_entries": cached_entries,
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
    """
    /diagnose workflow:

      1. Qwen performs a silent retrieval-only pass.
      2. Qwen is explicitly unloaded.
      3. The reasoning model runs in a fresh subprocess, trims evidence to
         fit its own actual context window, and performs the diagnosis.
      4. The primary model is reloaded.
      5. Only the user's diagnostic question and the reasoning model's final
         answer enter the persistent consultant conversation.
    """
    gathered_text = gather_context_for_diagnose(
        state,
        switcher,
        user_question,
    )

    analysis_system = build_diagnose_system_prompt()
    cached_entries = get_cached_context_entries(state)

    if state.reasoning_model_key is None:
        print(
            "⚠️  No --reasoning-model configured; "
            "running /diagnose with the primary model only."
        )

        llm, context_window = switcher.load(
            state.primary_model_key
        )

        analysis_user_content, budget_note = build_trimmed_evidence_block(
            llm,
            context_window,
            user_question,
            analysis_system,
            gathered_text,
            cached_entries,
        )

        if analysis_user_content is None:
            final_answer = None
            error = budget_note
        else:
            if budget_note:
                print(f"\nℹ️  [Consult] {budget_note}")

            analysis_messages = [
                {
                    "role": "system",
                    "content": analysis_system,
                },
                {
                    "role": "user",
                    "content": analysis_user_content,
                },
            ]

            check_context_guardrail(
                analysis_messages,
                llm,
                context_window,
            )

            response_content, is_truncated, interrupted = (
                stream_agent_response(
                    llm,
                    analysis_messages,
                    agent_label="\n🧠 [Diagnose]: ",
                )
            )

            final_answer = (
                sanitize_response(response_content)
                if not interrupted
                else None
            )

            error = (
                "Analysis was interrupted."
                if interrupted
                else None
            )

    else:
        print(
            f"\n🗑️  [Consult] Releasing GPU for the reasoning worker — "
            f"your hardware fits one ~7B model in VRAM at a time, so "
            f"{state.primary_model_key} needs to step aside temporarily..."
        )

        # Qwen must be fully released before the reasoning model starts.
        switcher.unload_current()

        try:
            print(
                "\n🧠 [Consult] Running reasoning model "
                "in an isolated process..."
            )

            final_answer, error = run_reasoning_subprocess(
                state.reasoning_model_key,
                state.kv_quantization_type,
                state.models_dir,
                analysis_system,
                user_question,
                gathered_text,
                cached_entries,
            )

        finally:
            print(
                f"\n🔁 [Consult] Reloading "
                f"{state.primary_model_key}..."
            )

            switcher.load(state.primary_model_key)

    # Persist only the real user request and the final reasoning-model answer.
    # Do not persist the temporary Qwen retrieval conversation.
    state.messages.append({
        "role": "user",
        "content": f"/diagnose {user_question}",
    })

    if error:
        print(
            f"\n⚠️  [Consult] Diagnose failed: {error}"
        )

        state.messages.append({
            "role": "system",
            "content": (
                f"[Automated /diagnose note — not something you said, do not "
                f"restate or repeat it verbatim: the diagnostic pass failed "
                f"with error: {error}]"
            ),
        })

    elif final_answer:
        # No recap print here: the answer was already streamed live above,
        # under the "[Reasoning]:" / "[Diagnose]:" label — printing it again
        # here would just duplicate it. Only the diff-safety check runs on
        # the text itself.
        if "SEARCH" in final_answer and "REPLACE" in final_answer:
            print(
                "\n⚠️  [Consult] This answer includes a code diff produced by "
                "a model with no ability to verify existing file contents. "
                "Cross-check every identifier and line it references against "
                "the actual files before applying it."
            )

        state.messages.append({
            "role": "system",
            "content": (
                "[Automated /diagnose note: a separate diagnostic analysis "
                "was already shown to the user for their question above. "
                "This was NOT generated by you and you did not say it — do "
                "not restate, repeat, or re-summarize it. Only refer to it "
                "if directly relevant to a future request, and even then, "
                "paraphrase briefly rather than reproducing it.]\n\n"
                f"{final_answer}"
            ),
        })

    else:
        print(
            "\n⚠️  [Consult] Diagnose produced no answer."
        )

        state.messages.append({
            "role": "system",
            "content": (
                "[Automated /diagnose note — not something you said: the "
                "diagnostic pass produced no answer for the user's question "
                "above.]"
            ),
        })

    state.expect_plain_text = False
    state.state2_violations = 0
    state.last_directive = None
