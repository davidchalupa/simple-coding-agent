MODEL_REGISTRY = {
    "qwen2.5-7b": {
        "filename": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        "display_name": "Qwen 2.5 Coder 7B (Q4_K_M)",
        "chat_format": "chatml",
        "max_context": 32768,
        "gpu_layers": -1  # -1 attempts to offload entirely to GPU
    },
    "qwen2.5-7b-q5km": {
        "filename": "Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf",
        "display_name": "Qwen 2.5 Coder 7B (Q5_K_M)",
        "chat_format": "chatml",
        "max_context": 32768,
        "gpu_layers": -1  # -1 attempts to offload entirely to GPU
    },
    "hermes3": {
        "filename": "Hermes-3-Llama-3.1-8B.Q4_K_M.gguf",
        "display_name": "Hermes 3 (Llama 3.1 8B)",
        "stop": ["<|im_end|>", "<|eot_id|>", "<|endoftext|>"],
        "temperature": 0.2,
        "max_context": 32768,
        "gpu_layers": -1,  # -1 or 99 offloads all layers to your RTX 5050 GPU
        "chat_format": "chatml"  # Hermes 3 uses standard ChatML formatting
    },
    "qwen2.5-14b": {
        "filename": "Qwen2.5-Coder-14B-Instruct-Q3_K_M.gguf",
        "display_name": "Qwen 2.5 Coder 14B (Q3_K_M)",
        "chat_format": "chatml",
        "max_context": 16384,
        "gpu_layers": [-1, 32, 24, 16],  # try full, then progressively more CPU spillover
    },
    "qwen3-8b": {
        "filename": "Qwen_Qwen3-8B-Q4_K_M.gguf",
        "display_name": "Qwen 3 8B",
        "chat_format": "chatml",
        "max_context": 32768,
        "gpu_layers": -1  # -1 attempts to offload entirely to GPU
    },
    "deepseek-r1-qwen-7b": {
        "filename": "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
        "display_name": "DeepSeek R1 Distill Qwen 7B",
        "chat_format": "chatml",
        "max_context": 32768,
        "gpu_layers": -1  # -1 attempts to offload entirely to GPU
    },
}
