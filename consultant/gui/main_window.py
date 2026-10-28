import os
import json
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextEdit, QPushButton, QLabel, QLineEdit, QApplication, QFileDialog
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from consultant.gui.consultant_worker import ConsultantWorker, PrepareWorker
from consultant.system_prompt_builder import build_consultant_system_prompt, build_diagnose_system_prompt
from model_registry import MODEL_REGISTRY
from consultant.model_switcher import ModelSwitcher
from common.context_hygiene_utils import get_token_footer


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

        # Track processing state for cursor feedback
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._is_processing = False
        self._in_prepare_mode = False

        self.init_ui()

    def init_ui(self):
        display_name = MODEL_REGISTRY.get(self.state.primary_model_key, {}).get("display_name",
                                                                                self.state.primary_model_key)
        self.setWindowTitle(f"Coding Consultant ({display_name})")
        self.resize(1100, 800)

        # === Initialize Menu Bar ===
        self.init_menu()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.web_view = QWebEngineView()
        # Enable JavaScript clipboard access
        self.web_view.settings().setAttribute(
            self.web_view.settings().WebAttribute.JavascriptCanAccessClipboard, True
        )
        self.load_chat_html()
        main_layout.addWidget(self.web_view, stretch=4)

        # === Approval Banner Widget (Initially Hidden) ===
        self.approval_widget = QWidget()
        self.approval_widget.setStyleSheet("""
            QWidget {
                background: #252526;
                border: 1px solid #cca700;
                border-radius: 6px;
                padding: 8px;
                color: #dcdcaa;
            }
            QPushButton {
                background: #333;
                color: #fff;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #444; }
            QPushButton#allow-btn { background: #0e639c; }
            QPushButton#allow-btn:hover { background: #1177bb; }
            QPushButton#deny-btn { background: #a1260d; }
            QPushButton#deny-btn:hover { background: #c53013; }
        """)
        approval_layout = QVBoxLayout(self.approval_widget)

        self.approval_label = QLabel()
        self.approval_label.setWordWrap(True)
        approval_layout.addWidget(self.approval_label)

        approval_btn_layout = QHBoxLayout()
        self.allow_btn = QPushButton("Allow (y)")
        self.allow_btn.setObjectName("allow-btn")
        self.allow_btn.clicked.connect(self.on_approval_allow)

        self.deny_btn = QPushButton("Deny (n)")
        self.deny_btn.setObjectName("deny-btn")
        self.deny_btn.clicked.connect(self.on_approval_deny)

        self.edit_btn = QPushButton("Edit / Feedback")
        self.edit_btn.clicked.connect(self.on_approval_edit_mode)

        approval_btn_layout.addWidget(self.allow_btn)
        approval_btn_layout.addWidget(self.deny_btn)
        approval_btn_layout.addWidget(self.edit_btn)
        approval_btn_layout.addStretch()
        approval_layout.addLayout(approval_btn_layout)

        # Feedback input row (hidden by default)
        self.feedback_container = QWidget()
        feedback_layout = QHBoxLayout(self.feedback_container)
        feedback_layout.setContentsMargins(0, 5, 0, 0)
        self.feedback_input = QLineEdit()
        self.feedback_input.setPlaceholderText("Enter feedback for denial / edit...")
        self.submit_feedback_btn = QPushButton("Submit Feedback")
        self.submit_feedback_btn.clicked.connect(self.on_approval_submit_feedback)
        feedback_layout.addWidget(self.feedback_input)
        feedback_layout.addWidget(self.submit_feedback_btn)
        self.feedback_container.hide()
        approval_layout.addWidget(self.feedback_container)

        self.approval_widget.hide()
        main_layout.addWidget(self.approval_widget)
        # ===============================================

        input_layout = QHBoxLayout()
        self.input_box = QTextEdit()
        self.input_box.setPlaceholderText("Type your prompt here... (Press Send or Ctrl+Enter)")
        self.input_box.setMaximumHeight(120)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.handle_send)
        self.send_button.setMinimumHeight(40)

        input_layout.addWidget(self.input_box)
        input_layout.addWidget(self.send_button)

        # Create status label at bottom of window
        self.token_status_label = QLabel()
        self.token_status_label.setStyleSheet("""
            QLabel {
                background: #1e1e1e;
                color: #888;
                font-style: italic;
                font-size: 13px;
                padding: 6px 12px;
                border-top: 1px solid #333;
            }
        """)
        self.token_status_label.setText("Ready")
        main_layout.addWidget(self.token_status_label)

        main_layout.addLayout(input_layout, stretch=1)

    def init_menu(self):
        """Creates the application menu bar with File operations."""
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        load_workflow_action = file_menu.addAction("&Load Prepared Workflow...")
        load_workflow_action.setShortcut("Ctrl+O")
        load_workflow_action.triggered.connect(self.open_prepare_file_dialog)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("E&xit")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

    def open_prepare_file_dialog(self):
        """Opens a file picker dialog to select and run a prepared workflow file."""
        if self.worker and self.worker.isRunning():
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Prepared Workflow File",
            os.getcwd(),
            "Workflow/Text Files (*.txt *.md);;All Files (*)"
        )

        if file_path:
            self.run_prepare_workflow(file_path)

    def run_prepare_workflow(self, macro_file_path):
        """Shared worker initializer for both /prepare macro text and File menu dialog."""
        user_text = f"/prepare {macro_file_path}"
        escaped_json = json.dumps(user_text)
        self.web_view.page().runJavaScript(f"appendUserMessage({escaped_json});")

        self.send_button.setEnabled(False)

        # Track processing for cursor feedback
        self._is_processing = True
        self._in_prepare_mode = True

        self.worker = PrepareWorker(
            self.state, macro_file_path, self.coder_prompt, self.reasoner_prompt, self.switcher
        )
        self.worker.chunk_received.connect(self.on_chunk)
        self.worker.status_received.connect(self.on_status)
        self.worker.system_message_received.connect(self.on_system_message)
        self.worker.step_finished.connect(self.on_turn_finished)
        self.worker.macro_finished.connect(self.on_macro_finished)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.token_footer_received.connect(self.on_token_footer)
        self.worker.start()

    def enterEvent(self, event):
        """Change cursor when entering the main window"""
        if not self._is_processing and (not self.worker or self.send_button.isEnabled()):
            QApplication.restoreOverrideCursor()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Restore cursor when leaving the main window"""
        if self._is_processing:
            QApplication.restoreOverrideCursor()
        super().leaveEvent(event)

    def load_chat_html(self):
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/4.3.0/marked.min.js"></script>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/styles/github-dark.min.css">
            <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.8.0/highlight.min.js"></script>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #1e1e1e; color: #d4d4d4; padding: 20px; line-height: 1.6; }
                .message { margin-bottom: 20px; padding: 12px 16px; border-radius: 8px; word-break: break-word; }
                .user { background: #2d2d2d; border-left: 4px solid #0e639c; white-space: pre-wrap; }
                .assistant { background: #252526; border-left: 4px solid #4ec9b0; }
                .status-badge { color: #888; font-style: italic; font-size: 13px; margin: 10px 0; padding: 4px 8px; }
                .system-notification { background: #222; border-left: 4px solid #cca700; padding: 8px 12px; border-radius: 4px; margin-bottom: 20px; font-size: 13px; color: #dcdcaa; }

                /* Code Snippet Wrapper & Header Styling */
                .code-wrapper {
                    background: #1e1e1e;
                    border: 1px solid #333;
                    border-radius: 6px;
                    margin: 14px 0;
                    overflow: hidden;
                }
                .code-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    background: #2d2d2d;
                    padding: 6px 12px;
                    font-size: 11px;
                    color: #858585;
                    border-bottom: 1px solid #333;
                }
                .code-lang {
                    font-weight: bold;
                    letter-spacing: 0.5px;
                }
                .copy-btn {
                    background: #3c3c3c;
                    color: #cccccc;
                    border: none;
                    padding: 3px 8px;
                    border-radius: 3px;
                    cursor: pointer;
                    font-size: 11px;
                    transition: background 0.2s, color 0.2s;
                }
                .copy-btn:hover {
                    background: #0e639c;
                    color: #ffffff;
                }
                pre {
                    margin: 0 !important;
                    background: #1e1e1e !important;
                    padding: 12px !important;
                    border-radius: 0 !important;
                    border: none !important;
                    overflow-x: auto;
                }
                code { font-family: Consolas, monospace; }
            </style>
        </head>
        <body>
            <div id="chat-container"></div>
            <script>
                let currentAssistantDiv = null;

                window.appendUserMessage = function(text) {
                    clearStatus();
                    const container = document.getElementById('chat-container');
                    const div = document.createElement('div');
                    div.className = 'message user';
                    div.innerText = text;
                    container.appendChild(div);
                    window.scrollTo(0, document.body.scrollHeight);
                };

                window.appendSystemMessage = function(text) {
                    clearStatus();
                    const container = document.getElementById('chat-container');
                    const div = document.createElement('div');
                    div.className = 'system-notification';
                    div.innerText = '⚙️ ' + text;
                    container.appendChild(div);
                    window.scrollTo(0, document.body.scrollHeight);
                };

                window.showStatus = function(text) {
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
                };

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

                window.appendChunk = function(text) {
                    if (!currentAssistantDiv) {
                        startAssistantMessage();
                    }
                    currentAssistantDiv.innerText += text;
                    window.scrollTo(0, document.body.scrollHeight);
                };

                window.finalizeAssistantMessage = function(fullText) {
                    clearStatus();
                    if (!currentAssistantDiv) {
                        startAssistantMessage();
                    }

                    let cleanedText = fullText.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '').trim();
                    if (!cleanedText) cleanedText = fullText;

                    // 1. Native Markdown parsing via marked.js
                    let parsedHtml = '';
                    if (typeof marked !== 'undefined') {
                        parsedHtml = marked.parse(cleanedText);
                    } else {
                        parsedHtml = '<pre><code>' + cleanedText.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") + '</code></pre>';
                    }

                    currentAssistantDiv.innerHTML = parsedHtml;

                    // 2. Post-process DOM to wrap <pre> blocks with IDE header and Copy button
                    currentAssistantDiv.querySelectorAll('pre').forEach(pre => {
                        if (!pre.parentNode.classList.contains('code-wrapper')) {
                            const code = pre.querySelector('code');
                            let lang = 'CODE';
                            if (code) {
                                const classList = Array.from(code.classList);
                                const langClass = classList.find(c => c.startsWith('language-'));
                                if (langClass) {
                                    lang = langClass.replace('language-', '').toUpperCase();
                                }
                            }

                            const wrapper = document.createElement('div');
                            wrapper.className = 'code-wrapper';

                            const header = document.createElement('div');
                            header.className = 'code-header';

                            const titleSpan = document.createElement('span');
                            titleSpan.className = 'code-lang';
                            titleSpan.innerText = lang;
                            header.appendChild(titleSpan);

                            const btn = document.createElement('button');
                            btn.className = 'copy-btn';
                            btn.innerText = 'Copy';
                            btn.onclick = () => {
                                const textToCopy = code ? code.innerText : pre.innerText;
                                
                                // Robust clipboard fallback for QWebEngineView
                                const textarea = document.createElement('textarea');
                                textarea.value = textToCopy;
                                textarea.style.position = 'fixed';  // Prevent scrolling to bottom
                                textarea.style.opacity = '0';
                                document.body.appendChild(textarea);
                                textarea.focus();
                                textarea.select();
                                
                                try {
                                    const successful = document.execCommand('copy');
                                    btn.innerText = successful ? 'Copied!' : 'Failed';
                                } catch (err) {
                                    console.error('Failed to copy text: ', err);
                                    btn.innerText = 'Error';
                                }
                                
                                document.body.removeChild(textarea);
                                setTimeout(() => btn.innerText = 'Copy', 2000);
                            };
                            header.appendChild(btn);

                            pre.parentNode.insertBefore(wrapper, pre);
                            wrapper.appendChild(header);
                            wrapper.appendChild(pre);
                        }
                    });

                    // 3. Apply syntax highlighting
                    if (typeof hljs !== 'undefined') {
                        currentAssistantDiv.querySelectorAll('pre code').forEach((block) => {
                            hljs.highlightElement(block);
                        });
                    }

                    currentAssistantDiv = null;
                    window.scrollTo(0, document.body.scrollHeight);
                };
            </script>
        </body>
        </html>
        """
        self.web_view.setHtml(html_content, QUrl("http://localhost"))

    # === Approval Banner Handlers ===
    def show_approval_panel(self, tool_name, display_args):
        self.approval_label.setText(
            f"<b>⚠️ Consultant requests execution of:</b> <code>{tool_name}</code><br/><b>Arguments:</b> {display_args}")
        self.feedback_container.hide()
        self.feedback_input.clear()
        self.approval_widget.show()

    def on_approval_allow(self):
        self.approval_widget.hide()
        if self.worker:
            self.worker.provide_approval('y')

    def on_approval_deny(self):
        self.approval_widget.hide()
        if self.worker:
            self.worker.provide_approval('n')

    def on_approval_edit_mode(self):
        self.feedback_container.show()
        self.feedback_input.setFocus()

    def on_approval_submit_feedback(self):
        feedback = self.feedback_input.text().strip()
        self.approval_widget.hide()
        if self.worker:
            self.worker.provide_approval('edit', feedback)

    # ===============================

    def handle_send(self):
        user_text = self.input_box.toPlainText().strip()
        if not user_text or (self.worker and self.worker.isRunning()):
            return

        # Track processing start for cursor feedback
        self._is_processing = True

        # ALWAYS show wait cursor when starting processing
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)  # <-- Remove the if condition!

        self.input_box.clear()

        if user_text.startswith("/mode "):
            mode = user_text[len("/mode "):].strip()
            if mode in ["think", "code"]:
                if mode == "think" and self.state.reasoning_model_key is None:
                    escaped_json = json.dumps("⚠️ Reasoning model is not configured. Cannot switch to think mode.")
                    self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")
                    return

                self.state.active_mode = mode
                active_model = self.state.reasoning_model_key if mode == "think" else self.state.primary_model_key
                llm, context_window = self.switcher.load(active_model)

                display_name = MODEL_REGISTRY.get(active_model, {}).get("display_name", active_model)
                self.setWindowTitle(f"Coding Consultant ({display_name}) - [{mode.upper()} MODE]")

                escaped_json = json.dumps(f"Switched active mode to: {mode} ({display_name})")
                self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")

                # emit token info for mode switch
                footer_text = get_token_footer(self.state.messages, llm, context_window)

                # Create a temporary worker just to emit the footer (or call directly)
                self.token_status_label.setText(footer_text.strip())  # Direct update for mode switch

                # Restore normal cursor and enable send button for mode switch
                QApplication.restoreOverrideCursor()
                self.send_button.setEnabled(True)

                return
            else:
                escaped_json = json.dumps("Unknown mode. Use '/mode code' or '/mode think'.")
                self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")
                return

        if user_text.startswith("/prepare "):
            macro_file_path = user_text[len("/prepare "):].strip()
            self.input_box.clear()
            self.run_prepare_workflow(macro_file_path)
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

        # Track processing for cursor feedback
        self._is_processing = True

        escaped_json = json.dumps(user_text)
        self.web_view.page().runJavaScript(f"appendUserMessage({escaped_json});")

        self.worker = ConsultantWorker(
            self.state, user_text, self.coder_prompt, self.reasoner_prompt, self.switcher
        )
        self.worker.chunk_received.connect(self.on_chunk)
        self.worker.status_received.connect(self.on_status)
        self.worker.turn_finished.connect(self.on_turn_finished)
        self.worker.token_footer_received.connect(self.on_token_footer)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.approval_requested.connect(self.show_approval_panel)
        self.worker.start()

    def on_chunk(self, text):
        escaped_json = json.dumps(text)
        self.web_view.page().runJavaScript(f"appendChunk({escaped_json});")

    def on_status(self, msg):
        escaped_json = json.dumps(msg)
        self.web_view.page().runJavaScript(f"showStatus({escaped_json});")

    def on_turn_finished(self, _):
        # Restore normal cursor when done processing (skip during prepare macro)
        if self._is_processing and not self._in_prepare_mode:
            QApplication.restoreOverrideCursor()

        QTimer.singleShot(10, lambda: self.send_button.setEnabled(True))
        self.send_button.setEnabled(True)

        if self.state.messages and self.state.messages[-1]["role"] == "assistant":
            last_msg = self.state.messages[-1]["content"]
            escaped_json = json.dumps(last_msg)
            self.web_view.page().runJavaScript(f"finalizeAssistantMessage({escaped_json});")

    def on_error(self, err_msg):
        # Skip cursor restoration during prepare macro (let it finish naturally)
        if self._is_processing and not self._in_prepare_mode:
            QApplication.restoreOverrideCursor()

        self.send_button.setEnabled(True)
        error_text = f"⚠️ Error: {err_msg}"
        escaped_json = json.dumps(error_text)
        self.web_view.page().runJavaScript(f"finalizeAssistantMessage({escaped_json});")

    def on_system_message(self, msg):
        escaped_json = json.dumps(msg)
        self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")

    def on_token_footer(self, footer_text):
        # Update the status label at bottom of window
        self.token_status_label.setText(footer_text.strip())

    def on_macro_finished(self, msg):
        # Restore normal cursor when done processing
        if self._is_processing:
            QApplication.restoreOverrideCursor()

        self.send_button.setEnabled(True)

        # reset the flag for /prepare macro (harmless in case of other macro)
        self._in_prepare_mode = False
        # processing also needs to be reset
        self._is_processing = False

        escaped_json = json.dumps(msg)
        self.web_view.page().runJavaScript(f"appendSystemMessage({escaped_json});")
