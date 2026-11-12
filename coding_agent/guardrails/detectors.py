import re


def should_route_to_reasoner(prompt: str) -> tuple[bool, str]:
    """
    Refined routing heuristic:
    - Default: Stay on 7B-Coder for fast, precise, compiler-style generation.
    - Route to 9B-Reasoner for debugging logs, fuzziness, and architectural changes.
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

    # --- 2. DECISION GATING ---

    if has_diagnostic_logs:
        return True, "Reasoner Trigger: Traceback or test failure log detected. Requires root-cause diagnostic reasoning."

    if has_architecture_intent:
        return True, "Reasoner Trigger: Architectural or refactoring intent detected."

    if has_vague_umbrella_request:
        return True, "Reasoner Trigger: Vague umbrella request requires contextual inference (Test 4 pattern)."

    if is_open_ended_question:
        return True, "Reasoner Trigger: Open-ended design or conceptual question."

    # --- 3. DEFAULT (Route to 7B-Coder) ---
    # When prompt density is high, explicit symbols are provided, and no debugging is needed.
    return False, "Default: High-density, compiler-friendly prompt; routing to 7B-Coder for speed."
