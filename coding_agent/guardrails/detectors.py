import re


def should_route_to_reasoner(prompt: str) -> tuple[bool, str]:
    """
    Refined routing heuristic for the execution agent:
    - Default: Stay on 7B-Coder for fast, precise, compiler-style generation.
    - Route to 9B-Reasoner for:
        1. Tracebacks / test failure diagnostic logs.
        2. Architectural / structural refactoring intent.
        3. Vague umbrella requests (Test 4 pattern).
        4. Open-ended design / conceptual questions.
        5. Complex test suite creation, stateful mocking, and fixtures.
    """
    prompt_lower = prompt.lower()

    # --- 1. REASONER TRIGGERS (Route to 9B) ---

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

    # C. Vague umbrella requests (Test 4 pattern: "all required engine functions")
    has_vague_umbrella_request = bool(re.search(
        r'(all required|all necessary|appropriate functions|figure out how to|implement a solution that handles)',
        prompt_lower
    ))

    # D. Open-ended design or conceptual questions
    is_open_ended_question = bool(
        re.search(r'^(why does|how should|what is the best way to|explain how)', prompt_lower.strip()))

    # E. Test suite creation, stateful mocking, or fixture orchestration
    has_test_generation_intent = bool(re.search(
        r'(write a test file|test suite|unittest|mock|side_effect|magicmock|test cases?|test_run_game_loop)',
        prompt_lower
    ))

    # --- 2. DECISION GATING ---

    if has_diagnostic_logs:
        return True, "Reasoner Trigger: Traceback or test failure log detected. Requires root-cause diagnostic reasoning."

    if has_architecture_intent:
        return True, "Reasoner Trigger: Architectural or refactoring intent detected."

    if has_vague_umbrella_request:
        return True, "Reasoner Trigger: Vague umbrella request requires contextual inference (Test 4 pattern)."

    if is_open_ended_question:
        return True, "Reasoner Trigger: Open-ended design or conceptual question."

    if has_test_generation_intent:
        return True, "Reasoner Trigger: Complex test suite writing, mocking, or fixture orchestration detected."

    # --- 3. DEFAULT (Route to 7B-Coder) ---
    return False, "Default: High-density, compiler-friendly prompt; routing to 7B-Coder for speed."
