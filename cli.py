import argparse

def parse_cli_arguments(model_registry_keys):
    """
    Parses CLI arguments for the Coding Agent and returns the configuration variables.
    """
    parser = argparse.ArgumentParser(description="Coding Agent CLI")
    parser.add_argument("--model", type=str, default="qwen2.5-7b", choices=model_registry_keys,
                        help="Select the model to run from the registry.")
    parser.add_argument("--disable-replace", action="store_true",
                        help="Disable the patch_file tool (forces full file rewrites).")
    parser.add_argument("--force-testing", action="store_true",
                        help="Force automated test prompting.")
    parser.add_argument("--disable-self-verify", action="store_true",
                        help="Disable automatic post-write lint/import self-verification on .py files.")
    parser.add_argument("--disable-kv-quantization", action="store_true",
                        help="Disable llama_cpp.GGML_TYPE_Q8_0 KV cache quantization.")

    args, unknown = parser.parse_known_args()

    # Derived configuration variables
    model = args.model
    allow_patch = not args.disable_replace
    force_testing = args.force_testing
    self_verify_py_writes = not args.disable_self_verify
    disable_kv_quantization = args.disable_kv_quantization

    return {
        "model": model,
        "allow_patch": allow_patch,
        "force_testing": force_testing,
        "self_verify_py_writes": self_verify_py_writes,
        "disable_kv_quantization": disable_kv_quantization,
        "unknown": unknown
    }
