
# Simple Coding Agent & Consultant

A local, self-contained AI coding assistant suite designed for privacy-first development. It leverages lightweight Large Language Models (LLMs) to assist with code generation, refactoring, testing, and documentation without requiring cloud infrastructure or even an internet connection. The system consists of two distinct tools: the **Simple Coding Agent** (for writing/editing code) and the **Read-Only Coding Consultant** (for analyzing existing codebases).

## 🌟 Project Overview

The project provides a robust environment for local AI-assisted software development, focusing on safety, accuracy, and efficiency. It supports multiple LLM architectures (Qwen, Mistral, Phi, DeepSeek) via GGUF quantization formats, allowing it to run efficiently even on consumer hardware with GPU offloading support.

### Core Components
1.  **Simple Coding Agent (`simple_coding_agent.py`)**: An interactive coding assistant capable of generating code, modifying files safely using guardrails and self-verification, refactoring monolithic code via AST analysis, and automating test generation.
2.  **Read-Only Coding Consultant (`coding_consultant.py`)**: A diagnostic tool designed for analyzing existing repositories. It retrieves context (files, symbols) to answer questions or generate documentation without modifying the source code.

## 🛠️ Installation

### Prerequisites
*   Python 3.x installed on your system (tested with Python 3.10+)
*   Access to model files (`*.gguf` format). The repository includes examples in the `models/` directory, but you can download additional models using the provided downloader scripts or manually place them there.

### Dependencies
Install required dependencies via pip:
```bash
pip install psutil tqdm pytest requests llama_cpp_python
```

**Optional (for GPU acceleration with LLaMA C++ Python bindings):**
*   Follow specific installation instructions if you plan to use `install_llama_cpp_python_gpu.sh` for CUDA support. Ensure your system has compatible drivers and a supported NVIDIA GPU.

### Model Setup
The application uses a model registry (`model_registry.py`) to manage available models. You have two options:

**Option 1: Use the Interactive Installer (Recommended)**
```bash
python install_agent.py
```
This curses-based UI will:
*   Display all available models from `MODEL_REGISTRY`
*   Show which models are already downloaded in `./models/`
*   Allow you to select and download specific models with progress tracking

**Option 2: Manual Download via Script**
Download individual models using the downloader script:
```bash
python downloaders/download_gguf.py --model <MODEL_ID>
```
Example:
```bash
python downloaders/download_gguf.py --model qwen2.5-7b-q5km
```

### Model Setup
The application uses a model registry (`model_registry.py`) to manage available models. Ensure the selected `.gguf` files are present in the `models/` directory with matching filenames defined in the registry (e.g., `Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf`).

## 🚀 Getting Started

### Running the Simple Coding Agent
The agent operates via an interactive chat loop where you type commands directly into the running application.

1.  **Start the Agent**:
    ```bash
    python simple_coding_agent.py --model qwen2.5-7b-q5km
    ```
    *   `--model`: Selects the primary coding model (e.g., `qwen2.5-7b`, `mistral-nemo-12b`). Defaults to GPU-enabled models if available.
    *   `--reasoning-model`: Optional secondary model for complex diagnostic tasks or `/diagnose` commands.

2. **Example Usage**:
    Ask the agent to perform a well-defined coding task:
    ```bash
    Write a script into a file called lcs.py that finds a longest common subsequence of two given strings. Make sure it exercises a simple example when run.
    /send
    ```

3. **Interactive Commands**:
    Once running, use the following macros:
    *   `/send`: Submit your prompt for processing.
    *   `/requirements [path]`: Generate or update `requirements.txt` based on project dependencies.
    *   `/readme [--deep] [--deep-ast] [path]`: Create AI-driven documentation. Use `--deep` to include file contents, or `--deep-ast` to traverse function names and extract CLI command/flag interfaces via AST analysis.
    *   `/split [--execute] [filepath]`: Refactor monolithic code into smaller components using AST analysis. Add `--execute` to apply changes directly (requires approval).
    *   `/clear`: Clears conversation memory and resets the environment.
    *   `/quit`: Terminates the session.

### Running the Read-Only Coding Consultant
The consultant is ideal for analyzing codebases without risking unintended modifications. It supports a "Code" mode (default) and a "Think" mode (for deeper reasoning).

1.  **Start the Consultant**:
    ```bash
    python coding_consultant.py --model qwen2.5-7b-q5km [--reasoning-model qwen3.5-9b]
    ```

2. **Example Usage**:
    Ask questions about your codebase. The consultant will retrieve necessary files, analyze them, and provide answers or generate documentation based on the retrieved context. It strictly forbids write operations to ensure safety during analysis.
    ```bash
    Load `src/auth.py` into context.
    /send
   
    How does authentication implemented in this file work?
    /send
    ```

3.  **Interactive Commands**:
    *   `/send`: Submit your prompt for processing.
    *   `/mode code`: Switch to standard coding mode (default).
    *   `/mode think`: Switch to reasoning mode for complex analysis tasks if a `--reasoning-model` is configured.
    *   `/prepare <macro_file>`: Execute a sequence of inputs from a file, useful for preparing the context for more complex tasks requiring reading more files repetitively.
    *   `/clear`: Clears the read cache and conversation history.
    *   `/quit`: Terminates the session.

## ✨ Features & Capabilities

### Simple Coding Agent
*   **Safe Code Modification**: Utilizes `patch_file`, `replace_lines`, and `write_file` tools with strict guardrails to prevent syntax errors, import failures, or drastic file shrinkage.
*   **Self-Verification**: Automatically validates Python code changes against linters before committing them to disk (`--disable-self-verify` flag available).
*   **AST-Powered Refactoring**: Detects structural issues like "God Classes" and suggests refactoring strategies using Abstract Syntax Tree (AST) parsing.
*   **Automated Testing**: Can generate test cases for new or modified code functions upon request (`--force-testing`).
*   **Context Awareness**: Maintains a conversation history to understand the broader context of your project, reducing hallucinations in generated code.

### Read-Only Coding Consultant
*   **Read-Only Safety**: Explicitly disables write tools (`write_file`, `patch_file`, etc.) to prevent accidental data loss during analysis.
*   **Multi-Model Reasoning**: Supports switching between a primary coding model and a specialized reasoning model for complex queries without reloading the entire context from scratch (using KV cache persistence).
*   **Smart Context Retrieval**: Automatically fetches relevant files, symbols, or directory structures when answering questions to ensure accurate responses.
*   **Documentation Generation**: Can synthesize comprehensive documentation based on code structure and content (`/readme --deep-ast`).

### Shared Architecture & Guardrails
Both tools share a robust architecture designed for reliability:
*   **Guardrail System**: Prevents infinite loops, identical file rewrites, unread replacements, and context overflow.
*   **KV Cache Persistence**: Saves conversation state to disk between turns (when enabled) to reduce latency and token usage during long sessions.
*   **Tool Execution Safety**: All tool calls are validated against a strict JSON payload format before execution.

## 📂 Repository Structure
```text
simple-coding-agent/
├── cli.py                          # CLI argument parsing and configuration
├── simple_coding_agent.py          # Main entry point for the Coding Agent
├── coding_consultant.py            # Main entry point for the Consultant
├── model_registry.py               # Defines available models and configurations
├── requirements.txt                # Python dependencies
│
├── common/                         # Shared utilities (input handling, tool execution)
│   ├── execute_tool.py             # Tool runner logic
│   └── ...
│
├── coding_agent/                   # Agent-specific modules
│   ├── guardrails/                 # Safety checks and verification
│   ├── native_linter.py            # Python syntax/import validation
│   └── split_tools.py              # AST-based refactoring tools
│   └── ...
│
├── consultant/                     # Consultant-specific modules
│   ├── tool_parser.py              # Extracts read-only tool requests
│   └── model_switcher.py           # Manages multi-model switching logic
│   └── ...
│
├── models/                         # Location for .gguf model files
└── tests/                          # Test suites for agent and consultant
```

## ⚙️ Advanced Configuration (CLI Flags)
*   `--disable-replace`: Forces full file rewrites instead of patching. Useful if the agent struggles with partial updates.
*   `--kv-quantization-type`: Specifies KV cache quantization (`Q4_0`, `Q8_0`) to balance memory usage and speed (Default: `Q8_0`).
*   `--light-system-prompt`: Uses a less restrictive system prompt.
