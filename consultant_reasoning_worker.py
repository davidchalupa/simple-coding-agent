"""
Standalone reasoning worker for the consultant's /diagnose flow.

Run as a fresh subprocess per call so that model load/teardown relies on OS
process boundaries rather than llama.cpp/llama-cpp-python cleaning up
correctly in-process (which testing showed is not reliable when swapping
models within a single long-lived process).

Contract:
  argv[1]      = path to write the JSON result to
  stdin (text) = JSON payload: {
      "model_key": str,
      "kv_quantization_type": str | null,   # matches cli.py's raw value
      "models_dir": str,
      "system_prompt": str,
      "user_content": str
  }

Result file on success: {"content": "<final answer>", "error": null}
Result file on failure: {"content": null, "error": "<message>"}

stdout/stderr are inherited from the parent by default (subprocess.run with
no capture), so model-load progress and streamed tokens are visible live in
the parent's terminal exactly as before — only the final structured answer
is exchanged via the result file, to avoid scraping noisy stdout.
"""
import sys
import os
import json
import re

from pathlib import Path

# Deliberately duplicated (not imported) from coding_consultant.py: that
# module runs CLI parsing and model loading at import time, which we don't
# want triggered inside this worker.
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
STRAY_TOKEN_RE = re.compile(r"<\|im_start\|>\s*assistant\s*|<\|im_end\|>|<\|im_start\|>")


def sanitize_response(response_content: str) -> str:
    cleaned = THINK_BLOCK_RE.sub("", response_content)
    if "<think>" in cleaned and "</think>" not in cleaned:
        cleaned = cleaned.split("<think>")[0]
    cleaned = STRAY_TOKEN_RE.sub("", cleaned)
    return cleaned.strip()


def write_result(result_path: str, content, error):
    with open(result_path, "w") as f:
        json.dump({"content": content, "error": error}, f)


def main():
    if len(sys.argv) < 2:
        print("Usage: consultant_reasoning_worker.py <result_path>", file=sys.stderr)
        sys.exit(2)

    result_path = sys.argv[1]

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as e:
        write_result(result_path, None, f"Failed to parse input payload: {e}")
        sys.exit(1)

    # Imports that touch the model registry / llama.cpp happen after we can
    # already report a clean error, so a bad payload never looks like a
    # crash to the parent.
    from model_registry import MODEL_REGISTRY
    from common.llm_init import LLMInitializer
    from common.output_handler import stream_agent_response

    model_key = payload["model_key"]
    kv_quantization_type = payload.get("kv_quantization_type")
    models_dir = Path(payload["models_dir"])
    system_prompt = payload["system_prompt"]
    user_content = payload["user_content"]

    config = MODEL_REGISTRY[model_key]
    target_path = models_dir / config["filename"]
    display_name = config["display_name"]

    initializer = LLMInitializer(target_path, display_name, config, kv_quantization_type)

    try:
        initializer.initialize_agent()
    except SystemExit as e:
        write_result(result_path, None, f"Model failed to load (see log above for details).")
        sys.exit(1)
    except Exception as e:
        write_result(result_path, None, f"Model failed to load: {e}")
        sys.exit(1)

    if initializer.llm is None:
        write_result(result_path, None, "Model failed to load (see log above for details).")
        sys.exit(1)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    print("\n🧠 [Reasoning Worker] Analyzing...")
    try:
        response_content, is_truncated, interrupted = stream_agent_response(initializer.llm, messages)
    except Exception as e:
        write_result(result_path, None, f"Generation failed: {e}")
        sys.exit(1)

    if interrupted:
        write_result(result_path, None, "Generation was interrupted.")
        sys.exit(1)

    final_answer = sanitize_response(response_content)
    write_result(result_path, final_answer, None)
    sys.exit(0)


if __name__ == "__main__":
    main()
