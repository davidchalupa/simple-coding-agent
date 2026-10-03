#!/bin/bash
#export PYTHONPATH=$(pwd)
source .venv/bin/activate
python coding_consultant.py --model qwen2.5-7b-q5km --reasoning-model qwen3.5-9b
