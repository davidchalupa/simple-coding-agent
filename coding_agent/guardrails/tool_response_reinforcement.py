from coding_agent.guardrails.definitions import READ_ONLY_TOOLS


def manage_tool_response(tool_name, tool_args, tool_result, agent_flags, state, tool_reinforcement):
    FAILURE_SIGNALS = ("Traceback", "Error", "FAILED", "SyntaxError", "Exception", "FAIL:")

    if tool_name in READ_ONLY_TOOLS:
        if agent_flags.awaiting_fix:
            agent_flags.record_hit("fix_context_reminder", state.track_guardrail_hits)
            FIX_CONTEXT_REMINDER = (
                "\n\n[System note: you are still resolving the command failure from earlier in this "
                "turn. If this read confirmed the cause, apply the fix now with a real "
                "write_file/patch_file/replace_lines call — target whichever file actually has the "
                "bug (the source file or a test file you wrote). Then re-run the command to verify.]"
            )
            tool_reinforcement += FIX_CONTEXT_REMINDER
        else:
            agent_flags.record_hit("inspect_reminder", state.track_guardrail_hits)
            INSPECT_REMINDER = (
                "\n\n[System note: the above was a read-only inspection result. Only call "
                "write_file, append_file, patch_file, or replace_lines if the user explicitly "
                "asked for a code change in their most recent message. Otherwise, respond now "
                "in plain text summarizing what you found — do not emit a tool call.]"
            )
            tool_reinforcement += INSPECT_REMINDER

    elif tool_name == "run_cmd" and any(sig in str(tool_result) for sig in FAILURE_SIGNALS):
        agent_flags.record_hit("run_cmd_failure_reminder", state.track_guardrail_hits)

        command_run = tool_args.get("command", "").lower()
        test_keywords = ["pytest", "unittest", "test.py", "test_"]
        is_test_run = any(kw in command_run for kw in test_keywords)

        if is_test_run:
            tool_reinforcement += (
                "\n\n[System note: The test suite failed. Because you likely just wrote or modified "
                "this test, the test setup itself is often flawed. Consider these common testing principles:\n"
                "1. Global Scope: Local variables declared in a test function do not override module-level "
                "globals in imported files. Override imported module attributes directly (e.g., "
                "`import target_module; target_module.CONFIG_VAR = test_val`).\n"
                "2. Determinism: Avoid relying on randomized data generators or dynamic setup functions. "
                "Hardcode explicit, minimal static fixtures/states so test execution is 100% predictable.\n"
                "3. State Isolation: Ensure mock objects or test state mutations are completely reset "
                "between test cases to prevent cascading failures.\n\n"
                "Do NOT assume the production code is broken. Fix the test file first before modifying production code."
            )
        else:
            tool_reinforcement += (
                "\n\n[System note: The command failed. If you know the fix, apply it now to the source code."
            )

        # Enforce direct tool execution regardless of failure type
        tool_reinforcement += (
            " Apply the fix with a real write_file/patch_file/replace_lines tool call — do not describe "
            "the fix in a markdown code block. Do not call run_cmd again until the fix has actually been "
            "applied via a tool call.]"
        )

        agent_flags.awaiting_fix = True
        agent_flags.last_run_cmd_error = tool_result

    return tool_reinforcement
