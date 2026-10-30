import os
import json
import time
from common.guardrail_tools import check_context_guardrail
from common.output_handler import stream_agent_response
from consultant.tool_parser import extract_tool_requests
from consultant.tool_processor import process_tool_requests
from consultant.guardrail_tools import sanitize_response, is_pure_load_request

MAX_ANSWERING_VIOLATIONS_PER_TURN = 3


def summarize_loaded_targets(tool_requests):
    parts = []
    for req in tool_requests:
        name = req.get("name")
        args = req.get("args", {}) or {}
        filepath = args.get("filepath")
        symbol = args.get("symbol_name")
        dirpath = args.get("dir_path")
        if name == "read_symbol" and filepath and symbol:
            parts.append(f"`{symbol}` from `{os.path.basename(filepath)}`")
        elif filepath:
            parts.append(f"`{os.path.basename(filepath)}`")
        elif dirpath:
            parts.append(f"the directory listing for `{dirpath}`")

    seen = set()
    unique_parts = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique_parts.append(p)

    return ", ".join(unique_parts) if unique_parts else "the requested content"


def run_consultant_turn(
    state,
    user_input,
    llm,
    context_window,
    is_reasoner,
    on_chunk=None,
    on_status=None,
    on_message=None,
    approval_callback=None,
    stop_event=None
):
    """Unified execution loop containing all guardrails, violations tracking, and tool caps."""
    msg_content = user_input
    if is_pure_load_request(user_input):
        msg_content += "\n\n[NOTE: If multiple files or targets are requested above, you MUST issue a separate <tool_call> for EACH file or target in this single response.]"

    state.messages.append({"role": "user", "content": msg_content})

    real_tool_calls_this_turn = 0
    state.expect_plain_text = False
    state.answering_violations = 0
    state.last_directive = None
    state.consecutive_reading_turns = 0

    while True:
        check_context_guardrail(state.messages, llm, context_window)

        stream_kwargs = {}
        if on_chunk:
            step_content = []
            def step_on_chunk(text):
                step_content.append(text)
                current_text = "".join(step_content)
                if "<tool_call>" not in current_text:
                    on_chunk(text)
            stream_kwargs["agent_label"] = ""
            stream_kwargs["on_chunk"] = step_on_chunk
            if on_status:
                stream_kwargs["on_status"] = on_status
        else:
            stream_kwargs["agent_label"] = "\n🧠 [Agent]: " if is_reasoner else "\n💻 [Agent]: "

        if is_reasoner:
            stream_kwargs["repeat_penalty"] = 1.15
            stream_kwargs["enforce_duplicate_payload_check"] = False

        state.last_turn_timestamp = time.time()
        state.turn_start_tokens = sum(len(msg.get("content", "")) for msg in state.messages)

        response_content, is_truncated, interrupted = stream_agent_response(
            llm, state.messages, context_window, stop_event=stop_event, **stream_kwargs
        )

        if interrupted:
            break

        response_content = sanitize_response(response_content)
        state.messages.append({"role": "assistant", "content": response_content})

        tool_requests = extract_tool_requests(response_content)

        if tool_requests:
            if state.expect_plain_text:
                state.answering_violations += 1
                violation_msg = (f"\n🛑 [Consult Guardrail] Model attempted tool call(s) in Answering Mode "
                                 f"(violation {state.answering_violations}/{MAX_ANSWERING_VIOLATIONS_PER_TURN}). Blocked.")
                if on_message:
                    on_message(violation_msg)
                else:
                    print(violation_msg)

                if state.answering_violations >= MAX_ANSWERING_VIOLATIONS_PER_TURN:
                    reminder = state.last_directive or (
                        "Using only the context already retrieved above, answer the original "
                        "question directly."
                    )
                    state.messages.append({
                        "role": "user",
                        "content": (f"{reminder}\n\nDo not call any tools, and do not mention tools, "
                                    "rules, states, or these instructions — just give the answer "
                                    "as you would to a colleague.")
                    })
                    forced_msg = "\n💬 [Consult] Forcing plain-text answer after repeated Answering Mode violations."
                    if on_message:
                        on_message(forced_msg)
                    else:
                        print(forced_msg)

                    check_context_guardrail(state.messages, llm, context_window)
                    try:
                        response_content, is_truncated, interrupted = stream_agent_response(
                            llm, state.messages, context_window, **stream_kwargs
                        )
                        if not interrupted:
                            response_content = sanitize_response(response_content)
                            if extract_tool_requests(response_content):
                                fallback = (
                                    "I wasn't able to produce a plain-text answer after "
                                    "repeated attempts. The context should already be loaded "
                                    "above — try rephrasing your request."
                                )
                                if on_chunk:
                                    on_chunk(fallback)
                                else:
                                    print(f"\n[Agent]: {fallback}")
                                state.messages.append({"role": "assistant", "content": fallback})
                            else:
                                state.messages.append({"role": "assistant", "content": response_content})
                                fin_msg = "\n💬 [Consult] Agent finished (forced). Awaiting your next question."
                                if on_message:
                                    on_message(fin_msg)
                                else:
                                    print(fin_msg)
                    except Exception as e:
                        err_str = f"\n[Error during forced generation]: {e}"
                        if on_message:
                            on_message(err_str)
                        else:
                            print(err_str)
                    break

                reminder = state.last_directive or (
                    "The context you need is already available above in this conversation — "
                    "no need to fetch it again. Please answer the original question now."
                )
                state.messages.append({
                    "role": "user",
                    "content": (f"{reminder}\n\nNo need to fetch anything again — everything needed "
                                "is already above. Answer now, directly and in plain text, without "
                                "mentioning tools, rules, or these instructions.")
                })
                continue

            perfect_history = "\n".join(
                [f'<tool_call>{json.dumps(req)}</tool_call>' for req in tool_requests])
            state.messages[-1] = {"role": "assistant", "content": perfect_history}
        else:
            fin_msg = "\n💬 [Consult] Agent finished. Awaiting your next question."
            if on_message:
                on_message(fin_msg)
            else:
                print(fin_msg)
            break

        combined_results, real_tool_calls_this_turn = process_tool_requests(
            state, response_content, tool_requests, real_tool_calls_this_turn,
            approval_callback=approval_callback
        )

        if combined_results.strip():
            if is_pure_load_request(user_input):
                state.messages.append({
                    "role": "user",
                    "content": f"Tool Execution Results:\n{combined_results.strip()}"
                })
                summary = summarize_loaded_targets(tool_requests)
                synthesized_answer = (
                    f"Context loaded — {summary} is now available. "
                    f"What would you like to do next?"
                )
                if on_chunk:
                    on_chunk(synthesized_answer)
                else:
                    print(f"\n[Agent]: {synthesized_answer}")

                state.messages.append({"role": "assistant", "content": synthesized_answer})
                state.expect_plain_text = False
                state.answering_violations = 0
                state.last_directive = None
                break

            state.consecutive_reading_turns += 1
            if state.consecutive_reading_turns >= state.max_consecutive_reading_turns:
                max_read_msg = f"\n🛑 [Consult Guardrail] Maximum consecutive reading turns ({state.max_consecutive_reading_turns}) reached. Forcing answer."
                if on_message:
                    on_message(max_read_msg)
                else:
                    print(max_read_msg)

                directive = (
                    f'[SYSTEM DIRECTIVE: Maximum reading rounds reached. Fulfill the user\'s explicit request now: '
                    f'"{user_input}". Provide the complete solution or answer directly. DO NOT output any tool calls.]'
                )
                state.messages.append({
                    "role": "user",
                    "content": f"Tool Execution Results:\n{combined_results.strip()}\n\n{directive}"
                })
                state.expect_plain_text = True
                state.last_directive = directive
                continue

            state.messages.append({
                "role": "user",
                "content": f"Tool Execution Results:\n{combined_results.strip()}"
            })

        if real_tool_calls_this_turn >= state.max_tool_calls_per_turn:
            state.messages.append({
                "role": "user",
                "content": "Tool limit reached for this turn. Provide your final answer in plain text based on the retrieved context."
            })
            state.expect_plain_text = True
