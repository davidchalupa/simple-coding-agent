import argparse

def parse_cli_arguments(model_registry_keys):
    """
    Parses CLI arguments for the Coding Agent and returns the configuration variables.
    """
    parser = argparse.ArgumentParser(description="Coding Agent CLI")
    parser.add_argument("--model", type=str, default="qwen2.5", choices=model_registry_keys,
                        help="Select the model to run from the registry.")
    parser.add_argument("--disable-replace", action="store_true",
                        help="Disable the patch_file tool (forces full file rewrites).")
    parser.add_argument("--force-testing", action="store_true",
                        help="Force automated test prompting.")
    parser.add_argument("--disable-self-verify", action="store_true",
                        help="Disable automatic post-write lint/import self-verification on .py files.")

    args, unknown = parser.parse_known_args()

    # Derived configuration variables
    model = args.model
    allow_patch = not args.disable_replace
    force_testing = args.force_testing
    self_verify_py_writes = not args.disable_self_verify

    return {
        "model": model,
        "allow_patch": allow_patch,
        "force_testing": force_testing,
        "self_verify_py_writes": self_verify_py_writes,
        "unknown": unknown
    }
