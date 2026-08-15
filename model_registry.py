MODEL_REGISTRY = {
    "qwen2.5": {
        "filename": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        "display_name": "Qwen 2.5 Coder 7B",
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
    }
}
