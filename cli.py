import argparse
import llama_cpp
from enum import Enum


class KVQuantizationType(Enum):
    NONE = None
    Q8_0 = "GGML_TYPE_Q8_0"
    Q4_0 = "GGML_TYPE_Q4_0"


def has_gpu():
    return getattr(
        llama_cpp,
        "llama_supports_gpu_offload",
        lambda: False,
    )()


def parse_cli_arguments(model_registry_keys):
    """
    Parses CLI arguments for the Coding Agent and returns the configuration variables.
    """
    default_model = 'qwen2.5-7b-q5km' if has_gpu() else 'qwen2.5-7b'
    default_reasoning_model = 'qwen3.5-9b' if has_gpu() else None

    parser = argparse.ArgumentParser(description="Coding Agent CLI")
    parser.add_argument("--model", type=str, default=default_model, choices=model_registry_keys,
                        help="Select the model to run from the registry.")
    parser.add_argument("--reasoning-model", type=str, default=default_reasoning_model, choices=model_registry_keys,
                        help="Optional second model used for /diagnose in the consultant. "
                             "Loaded in place of --model at the Reading -> Answering transition, "
                             "then unloaded and swapped back. Omit to disable /diagnose.")
    parser.add_argument("--disable-replace", action="store_true",
                        help="Disable the patch_file tool (forces full file rewrites).")
    parser.add_argument("--force-testing", action="store_true",
                        help="Force automated test prompting.")
    parser.add_argument("--disable-self-verify", action="store_true",
                        help="Disable automatic post-write lint/import self-verification on .py files.")
    parser.add_argument("--kv-quantization-type", type=KVQuantizationType,
                        choices=[KVQuantizationType.NONE, KVQuantizationType.Q8_0, KVQuantizationType.Q4_0],
                        default=KVQuantizationType.Q8_0,
                        help="Specify KV cache quantization type (None, GGML_TYPE_Q8_0, GGML_TYPE_Q4_0). Default is GGML_TYPE_Q8_0.")
    parser.add_argument("--light-system-prompt", action="store_true",
                        help="Forces the agent to use a more restrictive system prompt: more stable for mechanical "
                             "coding tasks but also less suitable for more complex tasks.")

    args, unknown = parser.parse_known_args()

    # Derived configuration variables
    model = args.model
    reasoning_model = args.reasoning_model
    allow_patch = not args.disable_replace
    force_testing = args.force_testing
    self_verify_py_writes = not args.disable_self_verify
    kv_quantization_type = args.kv_quantization_type.value
    light_system_prompt = args.light_system_prompt

    return {
        "model": model,
        "reasoning_model": reasoning_model,
        "allow_patch": allow_patch,
        "force_testing": force_testing,
        "self_verify_py_writes": self_verify_py_writes,
        "kv_quantization_type": kv_quantization_type,
        "light_system_prompt": light_system_prompt,
        "unknown": unknown
    }
