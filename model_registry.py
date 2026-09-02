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
    "qwen2.5-7b-q6k": {
        "filename": "Qwen2.5-Coder-7B-Instruct-Q6_K.gguf",
        "display_name": "Qwen 2.5 Coder 7B (Q6_K)",
        "chat_format": "chatml",
        "max_context": 24576,
        "gpu_layers": -1
    },
    "qwen2.5-14b": {
        "filename": "Qwen2.5-Coder-14B-Instruct-Q3_K_S.gguf",
        "display_name": "Qwen 2.5 Coder 14B (Q3_K_S)",
        "chat_format": "chatml",
        "max_context": 16384,
        "gpu_layers": [-1, 32, 24, 16]
    },
    "qwen2.5-14b-q2k": {
        "filename": "Qwen2.5-Coder-14B-Instruct-Q2_K.gguf",
        "display_name": "Qwen 2.5 Coder 14B (Q2_K)",
        "chat_format": "chatml",
        "max_context": 16384,
        "gpu_layers": [-1, 32, 24, 16]
    },
    "mistral-nemo-12b": {
        "filename": "Mistral-Nemo-Instruct-2407-Q3_K_S.gguf",
        "display_name": "Mistral Nemo 12B Instruct (Q3_K_S)",
        "chat_format": "chatml",
        "max_context": 16384,
        "gpu_layers": [-1, 32, 24, 16]  # Full offload if using Q8_0 or Q4_0 KV cache
    },
    "mistral-nemo-12b-q2k": {
        "filename": "Mistral-Nemo-Instruct-2407-Q2_K.gguf",
        "display_name": "Mistral Nemo 12B Instruct (Q2_K)",
        "chat_format": "chatml",
        "max_context": 16384,
        "gpu_layers": -1  # Fits fully in 8GB VRAM with Q8_0 KV cache
    },
    "phi-4-14b": {
        "filename": "phi-4-Q3_K_S.gguf",
        "display_name": "Microsoft Phi-4 14B (Q3_K_S)",
        "chat_format": "chatml",  # Phi-4 maps well to ChatML in most agent UI frameworks
        "max_context": 16384,
        "gpu_layers": [-1, 32, 24, 16]
    },
    "phi-4-14b-q2k": {
        "filename": "phi-4-Q2_K.gguf",
        "display_name": "Microsoft Phi-4 14B (Q2_K)",
        "chat_format": "chatml",  # Phi-4 maps well to ChatML in most agent UI frameworks
        "max_context": 16384,
        "gpu_layers": [-1, 32, 24, 16]
    },
    "hermes3": {
        "filename": "Hermes-3-Llama-3.1-8B.Q4_K_M.gguf",
        "display_name": "Hermes 3 (Llama 3.1 8B)",
        "stop": ["<|im_end|>", "<|eot_id|>", "<|endoftext|>"],
        "temperature": 0.2,
        "max_context": 32768,
        "gpu_layers": -1,
        "chat_format": "chatml"  # Hermes 3 uses standard ChatML formatting
    },
    "codellama-13b": {
        "filename": "codellama-13b-instruct.Q3_K_S.gguf",
        "display_name": "CodeLlama 13B Instruct (Q3_K_S)",
        "chat_format": "llama-2",  # CodeLlama uses Llama-2 [INST] prompt formatting
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
