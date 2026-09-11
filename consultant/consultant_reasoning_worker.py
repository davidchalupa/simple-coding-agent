"""
Standalone reasoning worker for the consultant's /diagnose flow.

A fresh process is created for every reasoning call. This deliberately uses
the OS process boundary as the strongest possible model/CUDA resource cleanup
mechanism available to the application.

Contract:
  argv[1]      = path to write the JSON result to
  stdin (text) = JSON payload:
      {
          "model_key": str,
          "kv_quantization_type": str | null,
          "models_dir": str,
          "system_prompt": str,
          "question": str,
          "gathered_text": str,
          "cached_entries": [{"tool_name": str, "label": str, "content": str}, ...]
      }

  question/gathered_text/cached_entries arrive separately (not as one
  pre-assembled prompt) because the context-budget trimming can only be done
  correctly AFTER the model is loaded here — this is the only place the real
  CONTEXT_WINDOW and a working tokenizer are available. A model can land on a
  smaller context than its registry max (e.g. 10240 instead of 32768 if
  larger sizes fail to allocate), so trimming decided in the parent process
  would be guessing against the wrong number.

Result:
  {"content": "<final answer>", "error": null}
or
  {"content": null, "error": "<message>"}
"""

import sys
import os
import json
import re
import gc

from pathlib import Path


THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>",
    re.DOTALL,
)

STRAY_TOKEN_RE = re.compile(
    r"<\|im_start\|>\s*assistant\s*"
    r"|<\|im_end\|>"
    r"|<\|im_start\|>"
)


def sanitize_response(response_content: str) -> str:
    cleaned = THINK_BLOCK_RE.sub("", response_content)

    if "<think>" in cleaned and "</think>" not in cleaned:
        cleaned = cleaned.split("<think>")[0]

    cleaned = STRAY_TOKEN_RE.sub("", cleaned)

    return cleaned.strip()


def write_result(result_path: str, content, error):
    with open(result_path, "w") as f:
        json.dump(
            {
                "content": content,
                "error": error,
            },
            f,
        )


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: consultant_reasoning_worker.py <result_path>",
            file=sys.stderr,
        )
        sys.exit(2)

    result_path = sys.argv[1]

    try:
        payload = json.loads(sys.stdin.read())

    except Exception as e:
        write_result(
            result_path,
            None,
            f"Failed to parse input payload: {e}",
        )
        sys.exit(1)

    initializer = None

    try:
        # Deliberately import only what this worker actually needs.
        from model_registry import MODEL_REGISTRY
        from common.llm_init import LLMInitializer
        from common.output_handler import stream_agent_response
        from consultant.guardrail_tools import build_trimmed_evidence_block

        model_key = payload["model_key"]
        kv_quantization_type = payload.get("kv_quantization_type")
        models_dir = Path(payload["models_dir"])
        system_prompt = payload["system_prompt"]
        question = payload["question"]
        gathered_text = payload.get("gathered_text", "")
        cached_entries = payload.get("cached_entries", [])

        config = MODEL_REGISTRY[model_key]

        target_path = models_dir / config["filename"]
        display_name = config["display_name"]

        initializer = LLMInitializer(
            target_path,
            display_name,
            config,
            kv_quantization_type,
        )

        initializer.initialize_agent()

        if initializer.llm is None:
            write_result(
                result_path,
                None,
                "Model failed to load.",
            )
            sys.exit(1)

        # Trim evidence to fit the ACTUAL loaded context window (which may
        # be smaller than the registry's max_context if larger sizes failed
        # to allocate) — must happen here, after load, not in the parent.
        user_content, budget_note = build_trimmed_evidence_block(
            initializer.llm,
            initializer.CONTEXT_WINDOW,
            question,
            system_prompt,
            gathered_text,
            cached_entries,
        )

        if user_content is None:
            # Fresh evidence alone doesn't fit — refuse rather than attempt
            # a truncated, mid-file analysis.
            write_result(result_path, None, budget_note)
            sys.exit(1)

        if budget_note:
            print(f"\nℹ️  [Reasoning Worker] {budget_note}", flush=True)

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

        print(
            "\n🧠 [Reasoning Worker] Analyzing...",
            flush=True,
        )

        try:
            response_content, is_truncated, interrupted = (
                stream_agent_response(
                    initializer.llm,
                    messages,
                    agent_label="\n🧠 [Reasoning]: ",
                )
            )

        except Exception as e:
            print(
                f"\n❌ [Reasoning Worker] Generation failed: {e}",
                file=sys.stderr,
                flush=True,
            )

            write_result(
                result_path,
                None,
                f"Generation failed: {e}",
            )
            sys.exit(1)

        if interrupted:
            write_result(
                result_path,
                None,
                "Generation was interrupted.",
            )
            sys.exit(1)

        final_answer = sanitize_response(response_content)

        write_result(
            result_path,
            final_answer,
            None,
        )

        print(
            "\n🧠 [Reasoning Worker] Done.",
            flush=True,
        )

        # Explicit cleanup before process exit. The OS will also clean up
        # everything on exit, but this makes the worker well-behaved and gives
        # us useful behavior if it is ever reused in the future.
        return 0

    except KeyError as e:
        write_result(
            result_path,
            None,
            f"Invalid worker payload / model key: {e}",
        )
        return 1

    except Exception as e:
        write_result(
            result_path,
            None,
            f"Reasoning worker failed: {e}",
        )
        return 1

    finally:
        if initializer is not None:
            try:
                initializer.close()
            except Exception as e:
                print(
                    f"⚠️ [Reasoning Worker] Cleanup failed: {e}",
                    file=sys.stderr,
                )

            initializer = None

        gc.collect()


if __name__ == "__main__":
    sys.exit(main())
