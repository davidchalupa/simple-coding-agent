#!/bin/bash

# location of this script (for activation of .venv from elsewhere)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# export PYTHONPATH="$SCRIPT_DIR"

source "$SCRIPT_DIR/.venv/bin/activate"
python "$SCRIPT_DIR/coding_consultant_gui.py" --model qwen2.5-7b-q5km --reasoning-model qwen3.5-9b
