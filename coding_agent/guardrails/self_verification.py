import os

from coding_agent.guardrails.tools import (auto_heal_newline_escaping,run_self_verification,
                                           check_return_consistency, check_import_resolution, check_callback_arity,
                                           check_constant_closures, check_test_coverage_regression)


def handle_self_verification_and_healing(state, tool_name, tool_args, agent_flags,
                                         tool_reinforcement, was_mod, tool_result):
    if state.self_verify_py_writes and was_mod and tool_name in ["write_file", "append_file",
                                                                 "patch_file", "replace_lines"]:
        fp = tool_args.get("filepath", "")
        linter_error = run_self_verification(fp)

        fn = os.path.basename(fp).lower()
        is_test_file = fn.startswith("test_") or fn.endswith("_test.py")

        import_errors = []
        arity_errors = []
        constant_closure_errors = []
        return_warnings = []
        coverage_errors = []

        if not linter_error:
            import_errors = check_import_resolution(fp, state.session_cwd)
            arity_errors = check_callback_arity(fp, state.session_cwd)
            constant_closure_errors = check_constant_closures(fp)
            if is_test_file:
                coverage_errors = check_test_coverage_regression(fp)

            # return_consistency is the lowest-severity signal (style/robustness, not a
            # functional bug) — only compute/report it once everything else is clean, so
            # it never buries a real bug in the same message.
            if not (import_errors or arity_errors or constant_closure_errors):
                line_range = None
                if tool_name == "replace_lines":
                    line_range = (tool_args.get("start_line"), tool_args.get("end_line"))
                return_warnings = check_return_consistency(fp, line_range)

        def _amnesia_patch():
            # Redacts the model's own prior assistant message to prevent it re-deriving
            # a wrong guess (e.g. a hallucinated module name) from its own prior turn.
            if state.messages and state.messages[-1].get("role") == "assistant":
                old_content = state.messages[-1].get("content", "")
                if len(old_content) > 50:
                    state.messages[-1]["content"] = (
                        f"[Action logged: {tool_name} to {fp}. Full JSON payload redacted "
                        f"to prevent repetition collapse.]"
                    )

        if linter_error:
            agent_flags.repair_required = True
            agent_flags.record_hit("syntax_error", state.track_guardrail_hits)
            # --- AUTO-HEALER FOR JSON NEWLINE ESCAPING ---
            if "unterminated string literal" in linter_error:
                try:
                    healed, new_lines = auto_heal_newline_escaping(fp)
                    if healed:
                        with open(fp, "w", encoding="utf-8") as f:
                            f.writelines(new_lines)

                        # Re-verify after healing
                        linter_error = run_self_verification(fp)
                        if not linter_error:
                            print(
                                f"🔧 [Auto-Healer] Successfully repaired JSON newline escaping artifact in {os.path.basename(fp)}!")
                            agent_flags.repair_required = False
                            agent_flags.consecutive_lint_failures = 0
                            agent_flags.last_verification_failure = None
                            # Skip the rest of the failure block since it's fixed!
                            return True, tool_reinforcement
                except Exception as e:
                    print(f"⚠️ Auto-healer encountered an exception: {e}")
            # ---------------

            agent_flags.consecutive_lint_failures += 1
            print(f"🚨 [Self-Verification] FAILED on {os.path.basename(fp)}:\n{linter_error}")

            tool_reinforcement += f"\n\nSystem Alert: Syntax check failed:\n{linter_error}\nFix it."
            _amnesia_patch()

            if agent_flags.consecutive_lint_failures >= 3:
                print("🛑 [Circuit Breaker] Repeated lint failures. Forcing turn end.")
                state.messages.append(
                    {"role": "user", "content": f"Tool Result:\n{tool_result}{tool_reinforcement}"})
                return False, tool_reinforcement

        elif import_errors or arity_errors or constant_closure_errors or coverage_errors:
            agent_flags.repair_required = True

            sections = []
            if import_errors:
                agent_flags.record_hit("import_resolution", state.track_guardrail_hits)
                sections.append(
                    f"Unresolved import(s) — do NOT just add a comment claiming this is fixed, "
                    f"the module name is still wrong. Use search_codebase or list_tree to find "
                    f"the ACTUAL file that defines the symbol, then correct the import:\n"
                    + "\n".join(import_errors)
                )
            if arity_errors:
                agent_flags.record_hit("callback_arity", state.track_guardrail_hits)
                sections.append("Lambda/callback arity mismatch(es):\n" + "\n".join(arity_errors))
            if constant_closure_errors:
                agent_flags.record_hit("constant_closure", state.track_guardrail_hits)
                sections.append(
                    "Callback(s) that ignore their arguments and always return the same "
                    "result — this can cause an infinite loop if used as a repeated callback:\n"
                    + "\n".join(constant_closure_errors)
                )
            if coverage_errors:
                agent_flags.record_hit("test_coverage_regression", state.track_guardrail_hits)
                sections.append("Test coverage regression:\n" + "\n".join(coverage_errors))

            agent_flags.consecutive_lint_failures += 1
            msg = "\n\n".join(sections)
            print(f"🚨 [Self-Verification] Issues found in {os.path.basename(fp)}:\n{msg}")
            tool_reinforcement += f"\n\nSystem Alert: The following issues were found:\n{msg}\nFix ALL of the above."
            _amnesia_patch()

            if agent_flags.consecutive_lint_failures >= 3:
                print("🛑 [Circuit Breaker] Repeated verification failures. Forcing turn end.")
                state.messages.append(
                    {"role": "user", "content": f"Tool Result:\n{tool_result}{tool_reinforcement}"})
                return False, tool_reinforcement

        elif return_warnings:
            agent_flags.repair_required = True
            agent_flags.record_hit("return_consistency", state.track_guardrail_hits)
            agent_flags.consecutive_lint_failures += 1
            msg = "\n".join(return_warnings)
            print(f"🚨 [Self-Verification] Inconsistent return paths in {os.path.basename(fp)}:\n{msg}")
            tool_reinforcement += f"\n\nSystem Alert: Possible inconsistent return values:\n{msg}\nFix it."

            if agent_flags.consecutive_lint_failures >= 3:
                print("🛑 [Circuit Breaker] Repeated return-consistency failures. Forcing turn end.")
                state.messages.append(
                    {"role": "user", "content": f"Tool Result:\n{tool_result}{tool_reinforcement}"})
                return False, tool_reinforcement
        else:
            if agent_flags.consecutive_lint_failures > 0:
                print(f"✅ {os.path.basename(fp)} now passes checks.")
            agent_flags.consecutive_lint_failures, agent_flags.last_verification_failure = 0, None
            return True, tool_reinforcement

    return True, tool_reinforcement
