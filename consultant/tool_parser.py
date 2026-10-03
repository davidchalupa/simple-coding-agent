import re
import json

from consultant.guardrail_tools import fuzzy_extract_tool_calls


def extract_tool_requests(response_content):
    # 1. Strip internal thought processes safely before parsing.
    # This prevents accidentally executing hypothetical tools debated inside a <think> block.
    safe_content = re.sub(r'<think>.*?</think>', '', response_content, flags=re.DOTALL)

    # Catch unclosed <think> blocks (e.g. if the generation was interrupted mid-thought)
    if "<think>" in safe_content:
        safe_content = safe_content.split("<think>")[0]

    # 2. Extract tool requests using the sanitized content
    raw_calls = re.findall(r'<tool_call>(.*?)</tool_call>', safe_content, re.DOTALL)
    tool_requests = []

    if raw_calls:
        for call in raw_calls:
            try:
                parsed = json.loads(call)
                if "name" in parsed:
                    tool_requests.append(parsed)
            except json.JSONDecodeError:
                continue
    else:
        try:
            clean_content = safe_content
            if "```json" in clean_content:
                clean_content = clean_content.split("```json")[1].split("```")[0]
            elif "```" in clean_content:
                clean_content = clean_content.split("```")[1].split("```")[0]

            clean_content = clean_content.strip()
            if clean_content.startswith("[") and clean_content.endswith("]"):
                parsed_array = json.loads(clean_content)
                if isinstance(parsed_array, list):
                    for item in parsed_array:
                        if isinstance(item, dict) and "name" in item:
                            tool_requests.append(item)
        except Exception:
            pass

        if not tool_requests:
            tool_requests = fuzzy_extract_tool_calls(safe_content)

    return tool_requests