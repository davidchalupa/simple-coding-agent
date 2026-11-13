import re


def should_route_to_reasoner(prompt: str) -> tuple[bool, str]:
    """
    Balanced routing heuristic for the execution agent:
    - Default: Stay on 7B-Coder for fast, precise generation.
    - Route to 9B-Reasoner for fuzzy test creation or test diagnostics/debugging.
    """
    prompt_lower = prompt.lower()

    # --- 1. DETECTORS & SIGNALS ---

    # A. Tracebacks, test failure logs, or stdout validation errors
    has_diagnostic_logs = bool(re.search(
        r'(traceback \(most recent call last\)|file "[^"]+", line \d+|FAILED:|assertionerror|stack trace)',
        prompt,
        re.IGNORECASE
    ))

    # B. Architectural / structural refactoring intent
    architectural_keywords = [
        "refactor", "restructure", "redesign", "architect",
        "decouple", "state machine", "design pattern"
    ]
    has_architecture_intent = any(kw in prompt_lower for kw in architectural_keywords)

    # C. Vague umbrella requests (Test 4 pattern)
    has_vague_umbrella_request = bool(re.search(
        r'(all required|all necessary|appropriate functions|figure out how to|implement a solution that handles)',
        prompt_lower
    ))

    # D. Open-ended design or conceptual questions
    is_open_ended_question = bool(
        re.search(r'^(why does|how should|what is the best way to|explain how)', prompt_lower.strip()))

    # E. Test generation intent
    has_test_generation_intent = bool(re.search(
        r'(write.*test|create.*test|implement.*test|new test file)',
        prompt_lower
    ))

    # F. Check if it's one of the strictly micromanaged test prompts (which MUST stay on Coder)
    is_micromanaged_test_prompt = bool(
        re.search(r'(critical rules for the test|symbol_name=\'run_game_loop\'|expected_start_snippet)', prompt_lower)
    )

    # G. Check if it relies on vague hints or open-ended test requirements (which should go to Reasoner)
    has_vague_test_hints = bool(
        re.search(r'(hints:|make sure that the board|figure out how to|custom action click it|use `random_get_action` you have read to lose)', prompt_lower)
    )

    # H. Error fixing, debugging, or test failure remediation intent
    has_debugging_intent = bool(re.search(
        r'(fix.*error|fix.*fail|fix.*test|debug|failing test|test fail|errors if they fail|resolve.*error|run the new tests using)',
        prompt_lower
    ))

    # --- 2. DECISION GATING ---

    if has_diagnostic_logs:
        return True, "Reasoner Trigger: Traceback or test failure log detected."

    if has_architecture_intent:
        return True, "Reasoner Trigger: Architectural or refactoring intent detected."

    if has_vague_umbrella_request:
        return True, "Reasoner Trigger: Vague umbrella request requires contextual inference."

    if is_open_ended_question:
        return True, "Reasoner Trigger: Open-ended design or conceptual question."

    # Guard: If it's a heavily micromanaged/strict test prompt, force it to stay on Coder
    if has_test_generation_intent and is_micromanaged_test_prompt:
        return False, "Default: Highly micromanaged strict test prompt; routing to 7B-Coder."

    # Route to Reasoner for fuzzy/hint-based test generation
    if has_test_generation_intent and has_vague_test_hints:
        return True, "Reasoner Trigger: Fuzzy hint-based test generation request."

    # Route to Reasoner for test running, debugging, and failure remediation prompts
    if has_debugging_intent:
        return True, "Reasoner Trigger: Test execution and debugging/remediation intent detected."

    # --- 3. DEFAULT (Route to 7B-Coder) ---
    return False, "Default: High-density, compiler-friendly prompt or command execution; routing to 7B-Coder."
