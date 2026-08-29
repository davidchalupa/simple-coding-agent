import os
import json
import re

from coding_agent.tool_definitions import extract_code_blocks
from coding_agent import file_splitter
from coding_agent import native_linter

from collections import Counter


def _detect_repetition(significant_lines, window=24, threshold=6):
    """
    Detects degenerate repetition: the same significant line appearing
    `threshold`+ times within the trailing `window`. Matches on the full
    stripped line (not a truncated prefix), so legitimately similar-but-
    distinct lines (e.g. assertions differing only in a trailing argument)
    are not falsely flagged.
    """
    recent = significant_lines[-window:]
    if len(recent) < threshold:
        return False
    _, count = Counter(recent).most_common(1)[0]
    return count >= threshold


def stream_agent_response(llm, messages):
    print(f"\n[Agent]: ", end="", flush=True)
    content, finish_reason = "", None

    try:
        for chunk in llm.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=0.1,
                max_tokens=4096
        ):
            choice = chunk['choices'][0]
            finish_reason = choice.get('finish_reason') or finish_reason
            if 'content' in (delta := choice.get('delta', {})):
                new_text = delta['content']
                print(new_text, end="", flush=True)
                content += new_text

                # --- SAFE LOOP BREAKER ---
                # Catch both real line breaks and JSON-escaped literal '\n'
                is_real_newline = '\n' in new_text
                # Handle token fragmentation where '\' and 'n' arrive separately
                is_escaped_newline = '\\n' in new_text or (
                        new_text == 'n' and len(content) >= 2 and content[-2:] == '\\n')

                if is_real_newline or is_escaped_newline:
                    # Normalize JSON-escaped newlines to real newlines for the split.
                    # This is what lets the detector see repetition happening INSIDE
                    # an in-progress "content": "..." JSON string value, not just in
                    # plain streamed markdown/prose.
                    normalized_content = content.replace('\\n', '\n')

                    significant_lines = [
                        line.strip() for line in normalized_content.split('\n')
                        if len(line.strip()) > 10
                    ]

                    if _detect_repetition(significant_lines):
                        print("\n\n🛑 [System]: Repetition loop detected. Forcing halt.")
                        finish_reason = "repetition_loop"
                        break

    except KeyboardInterrupt:
        print("\n\n🛑 [Generation Interrupted by User]")
        if "<tool_call> " in content and "</tool_call>" not in content:
            content = re.sub(r"<tool_call>.*$", "", content, flags=re.DOTALL).strip()
        if content: messages.append({"role": "assistant", "content": content + " [Interrupted]"})
        return content, False, True

    if "<tool_call>" in content and "</tool_call>" not in content:
        content += "</tool_call>"
        print("</tool_call>", end="", flush=True)

    print()
    messages.append({"role": "assistant", "content": content})
    return content, (finish_reason == "length"), False


def handle_ast_extraction(content, split_file, sandbox_dir):
    """Intercepts JSON routing plan and extracts blocks deterministically."""
    match = re.search(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
    if not match:
        return False, None

    try:
        plan = json.loads(match.group(1))

        print("\n⚙️  [System] Intercepted JSON routing plan. Executing AST extraction natively...")

        results = [
            f"[{fn}]: {extract_code_blocks(split_file, os.path.join(sandbox_dir, fn), blocks)}"
            for fn, blocks in plan.items()
            if isinstance(blocks, list)
        ]

        report = "\n".join(results)
        print(report)

        return True, (
            "System Alert: AST Extraction successfully executed.\n"
            f"Results:\n{report}\n\n"
            "Next Step: Review the extracted files with the available tools if needed. "
            "Do not attempt to recreate the extracted methods. "
            "When the refactor is complete, output 'Refactor Phase Complete'."
        )

    except json.JSONDecodeError:
        print("\n❌ [System] Failed to parse JSON plan.")
        return True, (
            "System Alert: Your JSON block was invalid. "
            "Please output ONLY valid JSON in the ```json block."
        )


def verify_sandbox_health(split_file, sandbox_dir, messages):
    """Checks structural integrity and lints sandbox files."""
    print("\n⚙️  [System Guardrail] Analyzing sandbox refactoring health...")

    expected_files = []
    for msg in reversed(messages):
        content = msg.get("content", "")

        # 1. Try to find the XML tag first
        match = re.search(r"<blueprint>\s*(.*?)\s*</blueprint>", content, re.DOTALL)

        # 2. Fallback: If agent stubbornly used Markdown headers instead
        if not match:
            match = re.search(r"(?:###)?\s*BLUEPRINT\s*.*?```(?:json)?\s*\n(.*?)\n\s*```", content,
                              re.DOTALL | re.IGNORECASE)

        if match:
            try:
                # Clean up any residual markdown if it was wrapped inside the tags
                clean_json = re.sub(r'```json\s*|\s*```', '', match.group(1)).strip()
                plan = json.loads(clean_json)
                expected_files = list(plan.keys())
            except json.JSONDecodeError:
                pass
            break

    # Pass expected_files into the verifier
    passed, report = file_splitter.verify_refactor_integrity(split_file, sandbox_dir, expected_files)

    if passed:
        for root, _, files in os.walk(sandbox_dir):
            for file in files:
                if file.endswith('.py') and not file.startswith('.'):
                    err = native_linter.check_python_syntax_and_imports(os.path.join(root, file))
                    if err: return False, f"Dependency Error in '{file}':\n{err}\nUse tools to add missing imports."
    return passed, report
