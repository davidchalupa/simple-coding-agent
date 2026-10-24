import sys
import os
from PyQt6.QtWidgets import QApplication
from cli import parse_cli_arguments
from model_registry import MODEL_REGISTRY
from consultant.gui.main_window import ConsultantMainWindow


class ConsultantState:
    def __init__(self, parsed_args):
        self.messages = []
        self.session_cwd = os.getcwd() if 'os' in globals() else __import__('os').getcwd()
        self.consult_read_cache = {}
        self.forbidden_tools = frozenset({"write_file", "append_file", "patch_file", "replace_lines"})
        self.max_tool_calls_per_turn = 20

        self.primary_model_key = parsed_args["model"]
        self.reasoning_model_key = parsed_args["reasoning_model"]
        self.kv_quantization_type = parsed_args["kv_quantization_type"]
        self.models_dir = __import__('pathlib').Path(__file__).resolve().parent / "models"

        self.cache_persistence_enabled = True
        self.active_mode = "code"
        self.expect_plain_text = False
        self.answering_violations = 0
        self.last_directive = None
        self.consecutive_reading_turns = 0
        self.max_consecutive_reading_turns = 3
        self.last_turn_timestamp = None
        self.turn_start_tokens = 0


def main():
    parsed_args = parse_cli_arguments(MODEL_REGISTRY.keys())
    state = ConsultantState(parsed_args)

    app = QApplication(sys.argv)
    window = ConsultantMainWindow(state)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
