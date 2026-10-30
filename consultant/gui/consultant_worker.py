import os
from PyQt6.QtCore import QThread, pyqtSignal, QEventLoop
import json
import time
from consultant.engine import run_consultant_turn
from consultant.input_and_macro_handlers import handle_user_input, handle_macros
from common.context_hygiene_utils import get_token_footer


class ConsultantWorker(QThread):
    chunk_received = pyqtSignal(str)
    status_received = pyqtSignal(str)
    turn_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    approval_requested = pyqtSignal(str, str)  # tool_name, display_args
    token_footer_received = pyqtSignal(str)

    def __init__(self, state, user_input, coder_system_prompt, reasoner_system_prompt, switcher):
        super().__init__()
        self.state = state
        self.user_input = user_input
        self.coder_system_prompt = coder_system_prompt
        self.reasoner_system_prompt = reasoner_system_prompt
        self.switcher = switcher

        self._approval_result = ('n', '')
        self._approval_loop = None

    def provide_approval(self, action, feedback=""):
        """Called by main window when user clicks Allow/Deny/Submit Feedback"""
        self._approval_result = (action, feedback)
        if self._approval_loop:
            self._approval_loop.quit()

    def _gui_approval_callback(self, tool_name, display_args):
        """Blocks worker thread safely using QEventLoop while waiting for GUI response"""
        self._approval_loop = QEventLoop()
        if not isinstance(display_args, str):
            display_args = json.dumps(display_args, indent=2) if isinstance(display_args, (dict, list)) else str(display_args)
        self.approval_requested.emit(tool_name, display_args)
        self._approval_loop.exec()
        return self._approval_result

    def run(self):
        try:
            import threading

            stop_event = threading.Event()

            def check_stop():
                while not stop_event.is_set() and not self.isFinished():
                    time.sleep(0.1)

                if stop_event.is_set():
                    raise KeyboardInterrupt("User requested stop")

            thread = threading.Thread(target=check_stop, daemon=True)
            thread.start()

            state = self.state
            switcher = self.switcher

            active_model_key = state.reasoning_model_key if state.active_mode == "think" else state.primary_model_key
            llm, context_window = switcher.load(active_model_key)
            is_reasoner = (state.active_mode == "think")

            # Add periodic stop checks inside the loop
            import time

            def run_with_stop_checks():
                from consultant.engine import run_consultant_turn

                while not stop_event.is_set() and not self.isFinished():
                    try:
                        run_consultant_turn(
                            state=state,
                            user_input=self.user_input,
                            llm=llm,
                            context_window=context_window,
                            is_reasoner=is_reasoner,
                            on_chunk=self.chunk_received.emit,
                            on_status=self.status_received.emit,
                            on_message=lambda msg: self.status_received.emit(msg.strip()),
                            approval_callback=self._gui_approval_callback
                        )
                    except KeyboardInterrupt as e:
                        if str(e) == "User requested stop":
                            raise
                        continue

            run_with_stop_checks()

            footer_text = get_token_footer(state.messages, llm, context_window)
            self.token_footer_received.emit(footer_text)

            self.turn_finished.emit("Done")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_occurred.emit(str(e))


class PrepareWorker(QThread):
    chunk_received = pyqtSignal(str)
    status_received = pyqtSignal(str)
    system_message_received = pyqtSignal(str)
    step_finished = pyqtSignal(str)
    macro_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    token_footer_received = pyqtSignal(str)

    def __init__(self, state, macro_file_path, coder_system_prompt, reasoner_system_prompt, switcher):
        super().__init__()
        self.state = state
        self.macro_file_path = macro_file_path
        self.coder_system_prompt = coder_system_prompt
        self.reasoner_system_prompt = reasoner_system_prompt
        self.switcher = switcher

    def run(self):
        if not os.path.exists(self.macro_file_path):
            self.error_occurred.emit(f"Prepare file not found: {self.macro_file_path}")
            return

        try:
            with open(self.macro_file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            self.error_occurred.emit(f"Failed to read macro file: {e}")
            return

        # Parse macro lines into discrete steps (grouping multi-line prompts via /send)
        steps = []
        current_chunk = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('/') and stripped != '/send':
                if current_chunk:
                    steps.append('\n'.join(current_chunk))
                    current_chunk = []
                steps.append(stripped)
            elif stripped == '/send':
                if current_chunk:
                    steps.append('\n'.join(current_chunk))
                    current_chunk = []
            else:
                current_chunk.append(line.rstrip('\n\r'))
        if current_chunk:
            steps.append('\n'.join(current_chunk))

        original_cwd = os.getcwd()
        self.state.consult_read_cache.clear()

        try:
            total_steps = len(steps)
            for idx, step_input in enumerate(steps, 1):
                self.system_message_received.emit(f"[Prepare Step {idx}/{total_steps}] Executing: {step_input}")

                if step_input.startswith('/'):
                    if handle_user_input(self.state, step_input, self.coder_system_prompt, self.switcher):
                        self.step_finished.emit("Step Done")
                        continue
                    if handle_macros(self.state, step_input, self.coder_system_prompt, self.reasoner_system_prompt, self.switcher):
                        self.step_finished.emit("Step Done")
                        continue

                active_model_key = self.state.reasoning_model_key if self.state.active_mode == "think" else self.state.primary_model_key
                llm, context_window = self.switcher.load(active_model_key)
                is_reasoner = (self.state.active_mode == "think")

                def macro_approval_callback(tool_name, display_args):
                    self.system_message_received.emit(f"[Prepare] Auto-approving tool call: {tool_name}")
                    return ('y', '')

                run_consultant_turn(
                    state=self.state,
                    user_input=step_input,
                    llm=llm,
                    context_window=context_window,
                    is_reasoner=is_reasoner,
                    on_chunk=self.chunk_received.emit,
                    on_status=self.status_received.emit,
                    on_message=lambda msg: self.status_received.emit(msg.strip()),
                    approval_callback=macro_approval_callback
                )

                self.step_finished.emit("Step Done")

            self.macro_finished.emit("Workflow completed. Context is now ready for interactive use.")

            footer_text = get_token_footer(self.state.messages, llm, context_window)
            self.token_footer_received.emit(footer_text)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_occurred.emit(str(e))
        finally:
            os.chdir(original_cwd)
