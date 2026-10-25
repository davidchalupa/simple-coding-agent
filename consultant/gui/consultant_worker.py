from PyQt6.QtCore import QThread, pyqtSignal, QEventLoop
import json
from consultant.engine import run_consultant_turn


class ConsultantWorker(QThread):
    chunk_received = pyqtSignal(str)
    status_received = pyqtSignal(str)
    turn_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    approval_requested = pyqtSignal(str, str)  # tool_name, display_args

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
            state = self.state
            switcher = self.switcher

            active_model_key = state.reasoning_model_key if state.active_mode == "think" else state.primary_model_key
            llm, context_window = switcher.load(active_model_key)
            is_reasoner = (state.active_mode == "think")

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

            self.turn_finished.emit("Done")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error_occurred.emit(str(e))
