import os
import sys
import psutil
from llama_cpp import Llama, llama_cpp


class LLMInitializer:
    def __init__(self, target_path, loaded_model_name, active_config):
        self.target_path = target_path
        self.loaded_model_name = loaded_model_name
        self.active_config = active_config
        self.llm = None
        self.CONTEXT_WINDOW = None

    def get_system_ram_gb(self):
        """Returns total system RAM in gigabytes."""
        return psutil.virtual_memory().total / (1024 ** 3)

    def initialize_agent(self):
        """Initializes LLM dynamically according to registry config, GPU, and RAM."""
        if self.llm is not None:
            return

        if not os.path.exists(self.target_path):
            print(f"❌ Error: Model file not found at {self.target_path}")
            sys.exit(1)

        print(f"Loading {self.loaded_model_name}...")

        total_ram = self.get_system_ram_gb()

        max_ctx = self.active_config["max_context"]
        base_gpu_contexts = [32768, 24576, 16384, 12288, 10240]
        gpu_contexts = sorted(list(set([min(ctx, max_ctx) for ctx in base_gpu_contexts])), reverse=True)

        if total_ram >= 24:
            cpu_contexts = gpu_contexts
        elif total_ram >= 12:
            cpu_contexts = [ctx for ctx in gpu_contexts if ctx <= 16384]
        else:
            cpu_contexts = [ctx for ctx in gpu_contexts if ctx <= 8192]

        has_gpu = getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)()

        configured_layers = self.active_config["gpu_layers"]
        gpu_layer_attempts = configured_layers if isinstance(configured_layers, list) else [configured_layers]

        if has_gpu:
            for n_layers in gpu_layer_attempts:
                for ctx_size in gpu_contexts:
                    try:
                        label = "full" if n_layers == -1 else f"partial ({n_layers} layers)"
                        print(f"🔄 Attempting GPU load [{label}] with {ctx_size} context...")
                        self.llm = Llama(
                            model_path=str(self.target_path),
                            n_ctx=ctx_size,
                            n_threads=6,
                            n_batch=512,
                            type_k=llama_cpp.GGML_TYPE_Q8_0,
                            type_v=llama_cpp.GGML_TYPE_Q8_0,
                            n_gpu_layers=n_layers,
                            chat_format=self.active_config["chat_format"],
                            flash_attn=True,
                            verbose=False
                        )
                        self.CONTEXT_WINDOW = ctx_size
                        print(f"🚀 Loaded on GPU [{label}] (Context: {self.CONTEXT_WINDOW}).")
                        break
                    except Exception as e:
                        print(f"⚠️ GPU load failed [{label}] at {ctx_size} context: {e}")
                if self.llm is not None:
                    break

        if self.llm is None:
            print(f"🐢 Running on CPU (Detected System RAM: {total_ram:.1f} GB)...")
            for ctx_size in cpu_contexts:
                try:
                    print(f"🔄 Attempting CPU load with {ctx_size} context...")
                    self.llm = Llama(
                        model_path=str(self.target_path),
                        n_ctx=ctx_size,
                        n_threads=6,
                        n_batch=512,
                        n_gpu_layers=0,
                        chat_format=self.active_config["chat_format"],
                        verbose=False
                    )
                    self.CONTEXT_WINDOW = ctx_size
                    print(f"🐢 Loaded on CPU (Context: {self.CONTEXT_WINDOW}).")
                    break
                except Exception as e_cpu:
                    print(f"⚠️ CPU allocation failed at {ctx_size} context: {e_cpu}")

        if self.llm is None:
            print("❌ Critical Error: Unable to initialize model on GPU or CPU.")
            sys.exit(1)
