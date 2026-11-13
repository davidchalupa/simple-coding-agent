import re


def should_route_to_reasoner(prompt: str) -> tuple[bool, str]:
    """
    Balanced routing heuristic for the execution agent:
    - Default: Stay on 7B-Coder for fast, precise generation.
    - Route to 9B-Reasoner for genuine ambiguity, diagnostics, fuzzy test creation, or error fixing/debugging.
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

    # E. Test generation intent (Requires creation verbs, excludes running/fixing commands)
    has_test_generation_intent = bool(re.search(
        r'(write.*test|create.*test|implement.*test|new test file)',
        prompt_lower
    ))

    # Check if the test creation prompt provides a strict, explicit recipe
    has_strict_test_recipe = bool(
        re.search(r'(side_effect\s*=|magicmock|expected_start_snippet|list comprehension)', prompt_lower))

    # Check if it relies on vague hints or open-ended test requirements
    has_vague_test_hints = bool(
        re.search(r'(hints:|make sure that the board|figure out how to|custom action click it)', prompt_lower))

    # === F. NEW: Error fixing, debugging, or test failure remediation intent ===
    has_debugging_intent = bool(re.search(
        r'(fix.*error|fix.*fail|fix.*test|debug|failing test|test fail|errors if they fail|resolve.*error)',
        prompt_lower
    ))

    # --- 2. DECISION GATING ---

    if has_diagnostic_logs:
        return True, "Reasoner Trigger: Traceback or test failure log detected."

    if has_architecture_intent:
        return True, "Reasoner Trigger: Architectural or refactoring intent detected."

    if has_vague_umbrella_request:
        return True, "Reasoner Trigger: Vague umbrella request requires contextual inference (Test 4 pattern)."

    if is_open_ended_question:
        return True, "Reasoner Trigger: Open-ended design or conceptual question."

    # Only route to Reasoner if it's explicitly creating a test AND lacks a recipe/uses vague hints.
    if has_test_generation_intent and (has_vague_test_hints or not has_strict_test_recipe):
        return True, "Reasoner Trigger: Fuzzy test generation request with missing recipe/explicit mock details."

    if has_debugging_intent:
        return True, "Reasoner Trigger: Error fixing or test failure debugging intent detected."

    # --- 3. DEFAULT (Route to 7B-Coder) ---
    return False, "Default: High-density, compiler-friendly prompt or command execution; routing to 7B-Coder."
