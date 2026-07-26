def display_welcome_banner(loaded_model_name, allow_patch):
    print("\n" + "═" * 60)
    print(f"🤖 Local Agent Initialized: [{loaded_model_name}]")
    if allow_patch:
        print("🔧 [STATUS] Patching Enabled")

    print("\n🚀 Available Modes & Macros:")
    print("  /requirements [--no-version] [path]")
    print("      -> Natively generate requirements.txt")
    print("\n  /readme [--conceptual] [--deep] [--deep-ast] [path]")
    print("      -> AI-driven repo documentation (Use --deep / -d for file contents & CLI parsing or --deep-ast for traversing function names)")
    print("\n  /split [--execute] [filepath]")
    print("      -> Refactor monoliths (Advisor mode or Logic Extraction mode)")
    print("         [--execute] adds risk: agent will attempt full code refactoring")

    print("\n⌨️  Commands:")
    print("  /send  -> Submit your prompt")
    print("  /clear -> Wipe conversation memory & reset environment")
    print("  /quit  -> Terminate agent")
    print("  CTRL+C -> Interrupt active text generation (best in native terminal)")
    print("═" * 60)
