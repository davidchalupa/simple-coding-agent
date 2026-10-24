import os
from PyQt6.QtCore import QThread, pyqtSignal
from common.guardrail_tools import check_context_guardrail
from common.output_handler import stream_agent_response
from consultant.tool_parser import extract_tool_requests
from consultant.tool_processor import process_tool_requests
from consultant.guardrail_tools import sanitize_response


class ConsultantWorker(QThread):
    chunk_received = pyqtSignal(str)
    status_received = pyqtSignal(str)
    turn_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, state, user_input, coder_system_prompt, reasoner_system_prompt, switcher):
        super().__init__()
        self.state = state
        self.user_input = user_input
        self.coder_system_prompt = coder_system_prompt
        self.reasoner_system_prompt = reasoner_system_prompt
        self.switcher = switcher

    def run(self):
        try:
            state = self.state
            user_input = self.user_input
            switcher = self.switcher

            active_model_key = state.reasoning_model_key if state.active_mode == "think" else state.primary_model_key
            llm, context_window = switcher.load(active_model_key)

            state.messages.append({"role": "user", "content": user_input})

            real_tool_calls_this_turn = 0
            state.expect_plain_text = False
            state.answering_violations = 0
            state.last_directive = None
            state.consecutive_reading_turns = 0

            is_reasoner = (state.active_mode == "think")

            while True:
                check_context_guardrail(state.messages, llm, context_window)

                step_content = []

                def step_on_chunk(text):
                    step_content.append(text)
                    current_text = "".join(step_content)
                    # Only stream to chat if we are NOT currently outputting a tool call block
                    if "<tool_call>" not in current_text:
                        self.chunk_received.emit(text)

                stream_kwargs = {
                    "agent_label": "",
                    "on_chunk": step_on_chunk,
                    "on_status": lambda msg: self.status_received.emit(msg)
                }

                if is_reasoner:
                    stream_kwargs["repeat_penalty"] = 1.15
                    stream_kwargs["enforce_duplicate_payload_check"] = False

                response_content, is_truncated, interrupted = stream_agent_response(
                    llm, state.messages, context_window, **stream_kwargs
                )

                if interrupted:
                    break

                response_content = sanitize_response(response_content)
                state.messages.append({"role": "assistant", "content": response_content})

                tool_requests = extract_tool_requests(response_content)

                if not tool_requests:
                    break

                # Emit a clean status indicator instead of raw tool JSON
                summary = self.summarize_targets(tool_requests)
                self.status_received.emit(f"🔍 Reading {summary}...")

                combined_results, real_tool_calls_this_turn = process_tool_requests(
                    state, response_content, tool_requests, real_tool_calls_this_turn
                )

                if combined_results.strip():
                    state.messages.append({
                        "role": "user",
                        "content": f"Tool Execution Results:\n{combined_results.strip()}"
                    })

            self.turn_finished.emit("Done")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_occurred.emit(str(e))

    def summarize_targets(self, tool_requests):
        parts = []
        for req in tool_requests:
            args = req.get("args", {}) or {}
            filepath = args.get("filepath")
            if filepath:
                parts.append(os.path.basename(filepath))
        return ", ".join(set(parts)) if parts else "files"
