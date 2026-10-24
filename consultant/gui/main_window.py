import json
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextEdit, QPushButton
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from consultant.gui.consultant_worker import ConsultantWorker
from consultant.system_prompt_builder import build_consultant_system_prompt, build_diagnose_system_prompt
from model_registry import MODEL_REGISTRY
from consultant.model_switcher import ModelSwitcher


class ConsultantMainWindow(QMainWindow):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.coder_prompt = build_consultant_system_prompt()
        self.reasoner_prompt = build_diagnose_system_prompt()
        self.switcher = ModelSwitcher(state.kv_quantization_type, state.models_dir)
        self.switcher.load(state.primary_model_key)

        if state.cache_persistence_enabled:
            self.switcher.ensure_cache_dir_exists()

        self.state.messages = [{"role": "system", "content": self.coder_prompt}]
        self.worker = None

        self.init_ui()

    def init_ui(self):
        display_name = MODEL_REGISTRY.get(self.state.primary_model_key, {}).get("display_name",
                                                                                self.state.primary_model_key)
        self.setWindowTitle(f"Coding Consultant ({display_name})")
        self.resize(1100, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.web_view = QWebEngineView()
        self.load_chat_html()
        main_layout.addWidget(self.web_view, stretch=4)

        input_layout = QHBoxLayout()
        self.input_box = QTextEdit()
        self.input_box.setPlaceholderText("Type your prompt here... (Press Send or Ctrl+Enter)")
        self.input_box.setMaximumHeight(120)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.handle_send)
        self.send_button.setMinimumHeight(40)

        input_layout.addWidget(self.input_box)
        input_layout.addWidget(self.send_button)
        main_layout.addLayout(input_layout, stretch=1)

    def load_chat_html(self):
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.js"></script>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/styles/github-dark.min.css">
            <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/highlight.min.js"></script>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #1e1e1e; color: #d4d4d4; padding: 20px; line-height: 1.6; }
                .message { margin-bottom: 20px; padding: 12px 16px; border-radius: 8px; word-break: break-word; }
                .user { background: #2d2d2d; border-left: 4px solid #0e639c; white-space: pre-wrap; }
                .assistant { background: #252526; border-left: 4px solid #4ec9b0; }
                .status-badge { color: #888; font-style: italic; font-size: 13px; margin: 10px 0; padding: 4px 8px; }
                .system-notification { background: #222; border-left: 4px solid #cca700; padding: 8px 12px; border-radius: 4px; margin-bottom: 20px; font-size: 13px; color: #dcdcaa; }
                pre { position: relative; background: #1e1e1e; padding: 12px; border-radius: 6px; overflow-x: auto; margin-top: 10px; margin-bottom: 10px; }
                code { font-family: Consolas, monospace; }
                .copy-btn { position: absolute; top: 8px; right: 8px; background: #333; color: #fff; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
                .copy-btn:hover { background: #555; }
            </style>
        </head>
        <body>
            <div id="chat-container"></div>
            <script>
                let currentAssistantDiv = null;

                function appendUserMessage(text) {
                    clearStatus();
                    const container = document.getElementById('chat-container');
                    const div = document.createElement('div');
                    div.className = 'message user';
                    div.innerText = text;
                    container.appendChild(div);
                    window.scrollTo(0, document.body.scrollHeight);
                }

                function appendSystemMessage(text) {
                    clearStatus();
                    const container = document.getElementById('chat-container');
                    const div = document.createElement('div');
                    div.className = 'system-notification';
                    div.innerText = '⚙️ ' + text;
                    container.appendChild(div);
                    window.scrollTo(0, document.body.scrollHeight);
                }

                function showStatus(text) {
                    const container = document.getElementById('chat-container');
                    let statusDiv = document.getElementById('active-status');
                    if (!statusDiv) {
                        statusDiv = document.createElement('div');
                        statusDiv.id = 'active-status';
                        statusDiv.className = 'status-badge';
                        container.appendChild(statusDiv);
                    }
                    statusDiv.innerText = text;
                    window.scrollTo(0, document.body.scrollHeight);
                }

                function clearStatus() {
                    const statusDiv = document.getElementById('active-status');
                    if (statusDiv) statusDiv.remove();
                }

                function startAssistantMessage() {
                    clearStatus();
                    const container = document.getElementById('chat-container');
                    currentAssistantDiv = document.createElement('div');
                    currentAssistantDiv.className = 'message assistant';
                    container.appendChild(currentAssistantDiv);
                }

                function appendChunk(text) {
                    if (!currentAssistantDiv) {
                        startAssistantMessage();
                    }
                    currentAssistantDiv.innerText += text;
                    window.scrollTo(0, document.body.scrollHeight);
                }

                function finalizeAssistantMessage(fullText) {
                    clearStatus();
                    if (!currentAssistantDiv) {
                        startAssistantMessage();
                    }

                    let cleanedText = fullText.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '').trim();
                    if (!cleanedText) cleanedText = fullText;

                    let parsedHtml = '';
                    if (typeof marked !== 'undefined') {
                        parsedHtml = marked.parse(cleanedText);
                    } else {
                        parsedHtml = '<pre style="white-space: pre-wrap;">' + 
                                     cleanedText.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") + 
                                     '</pre>';
                    }

                    currentAssistantDiv.innerHTML = parsedHtml;

                    if (typeof hljs !== 'undefined') {
                        currentAssistantDiv.querySelectorAll('pre code').forEach((block) => {
                            hljs.highlightElement(block);
                        });
                    }

                    currentAssistantDiv.querySelectorAll('pre').forEach(pre => {
                        if (!pre.querySelector('.copy-btn')) {
                            const btn = document.createElement('button');
                            btn.className = 'copy-btn';
                            btn.innerText = 'Copy';
                            btn.onclick = () => {
                                const codeBlock = pre.querySelector('code') || pre;
                                navigator.clipboard.writeText(codeBlock.innerText);
                                btn.innerText = 'Copied!';
                                setTimeout(() => btn.innerText = 'Copy', 2000);
                            };
                            pre.appendChild(btn);
                        }
                    });
                    currentAssistantDiv = null;
                    window.scrollTo(0, document.body.scrollHeight);
                }
            </script>
        </body>
        </html>
        """
        self.web_view.setHtml(html_content, QUrl("http://localhost"))

    def handle_send(self):
        user_text = self.input_box.toPlainText().strip()
        if not user_text or (self.worker and self.worker.isRunning()):
            return

        self.input_box.clear()

        # Intercept commands & macros
        if user_text.startswith("/mode "):
            mode = user_text[len("/mode "):].strip()
            if mode in ["think", "code"]:
                if mode == "think" and self.state.reasoning_model_key is None:
                    escaped_json = json.dumps("⚠️ Reasoning model is not configured. Cannot switch to think mode.")
                    self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")
                    return

                self.state.active_mode = mode
                active_model = self.state.reasoning_model_key if mode == "think" else self.state.primary_model_key

                # Immediately load model into VRAM and trigger console logs
                self.switcher.load(active_model)

                display_name = MODEL_REGISTRY.get(active_model, {}).get("display_name", active_model)
                self.setWindowTitle(f"Coding Consultant ({display_name}) - [{mode.upper()} MODE]")

                escaped_json = json.dumps(f"Switched active mode to: {mode} ({display_name})")
                self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")
                return
            else:
                escaped_json = json.dumps("Unknown mode. Use '/mode code' or '/mode think'.")
                self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")
                return

        if user_text == "/clear":
            self.state.messages = [{"role": "system", "content": self.coder_prompt}]
            self.web_view.page().runJavaScript("document.getElementById('chat-container').innerHTML = '';")
            escaped_json = json.dumps("Chat history cleared.")
            self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")
            return

        if user_text in ["/quit", "/exit"]:
            self.close()
            return

        escaped_json = json.dumps(user_text)
        self.web_view.page().runJavaScript(f"appendUserMessage({escaped_json});")

        self.worker = ConsultantWorker(
            self.state, user_text, self.coder_prompt, self.reasoner_prompt, self.switcher
        )
        self.worker.chunk_received.connect(self.on_chunk)
        self.worker.status_received.connect(self.on_status)
        self.worker.turn_finished.connect(self.on_turn_finished)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.start()

    def on_chunk(self, text):
        escaped_json = json.dumps(text)
        self.web_view.page().runJavaScript(f"appendChunk({escaped_json});")

    def on_status(self, msg):
        escaped_json = json.dumps(msg)
        self.web_view.page().runJavaScript(f"showStatus({escaped_json});")

    def on_turn_finished(self, _):
        self.send_button.setEnabled(True)
        if self.state.messages and self.state.messages[-1]["role"] == "assistant":
            last_msg = self.state.messages[-1]["content"]
            escaped_json = json.dumps(last_msg)
            self.web_view.page().runJavaScript(f"finalizeAssistantMessage({escaped_json});")

    def on_error(self, err_msg):
        self.send_button.setEnabled(True)
        error_text = f"⚠️ Error: {err_msg}"
        escaped_json = json.dumps(error_text)
        self.web_view.page().runJavaScript(f"finalizeAssistantMessage({escaped_json});")
