import os
import sys
import gc
import psutil
from enum import Enum

from llama_cpp import Llama, llama_cpp


class LLMInitializer:
    """
    Owns one Llama instance and all configuration needed to construct it.

    Cleanup is explicit so ModelSwitcher can reliably release the model
    before another model is constructed in the same process.
    """

    def __init__(self, target_path, loaded_model_name, active_config, kv_quantization_type):
        self.target_path = target_path
        self.loaded_model_name = loaded_model_name
        self.active_config = active_config

        self.llm = None
        self.CONTEXT_WINDOW = None

        self.type_k = None
        self.type_v = None

        if kv_quantization_type == "GGML_TYPE_Q8_0":
            self.type_k = llama_cpp.GGML_TYPE_Q8_0
            self.type_v = llama_cpp.GGML_TYPE_Q8_0

        elif kv_quantization_type == "GGML_TYPE_Q4_0":
            self.type_k = llama_cpp.GGML_TYPE_Q4_0
            self.type_v = llama_cpp.GGML_TYPE_Q4_0

    def get_system_ram_gb(self):
        return psutil.virtual_memory().total / (1024 ** 3)

    def close(self):
        """
        Explicitly release the Llama object and all Python references owned
        by this initializer.

        We deliberately do NOT call llama_backend_free() here. In current
        llama.cpp that function is not a CUDA-context reset.
        """
        llm = self.llm
        self.llm = None

        self.CONTEXT_WINDOW = None

        if llm is not None:
            close = getattr(llm, "close", None)

            if callable(close):
                try:
                    close()
                except Exception as e:
                    print(
                        f"⚠️ [{self.loaded_model_name}] "
                        f"Llama.close() failed: {e}"
                    )

            # Explicitly drop the local reference too.
            del llm

        # Collect Python objects that may own native llama.cpp resources.
        gc.collect()

    def initialize_agent(self):
        """Initialize the LLM according to registry config, GPU and RAM."""

        if self.llm is not None:
            return

        if not os.path.exists(self.target_path):
            print(
                f"❌ Error: Model file not found at {self.target_path}"
            )
            raise FileNotFoundError(self.target_path)

        print(f"Loading {self.loaded_model_name}...")

        total_ram = self.get_system_ram_gb()

        max_ctx = self.active_config["max_context"]

        base_gpu_contexts = [
            32768,
            24576,
            16384,
            12288,
            10240,
        ]

        gpu_contexts = sorted(
            list(
                set(
                    min(ctx, max_ctx)
                    for ctx in base_gpu_contexts
                )
            ),
            reverse=True,
        )

        if total_ram >= 24:
            cpu_contexts = gpu_contexts

        elif total_ram >= 12:
            cpu_contexts = [
                ctx for ctx in gpu_contexts
                if ctx <= 16384
            ]

        else:
            cpu_contexts = [
                ctx for ctx in gpu_contexts
                if ctx <= 8192
            ]

        has_gpu = getattr(
            llama_cpp,
            "llama_supports_gpu_offload",
            lambda: False,
        )()

        configured_layers = self.active_config["gpu_layers"]

        gpu_layer_attempts = (
            configured_layers
            if isinstance(configured_layers, list)
            else [configured_layers]
        )

        # ------------------------------------------------------------------
        # GPU path
        # ------------------------------------------------------------------

        if has_gpu:
            for n_layers in gpu_layer_attempts:
                for ctx_size in gpu_contexts:
                    candidate = None

                    try:
                        label = (
                            "full"
                            if n_layers == -1
                            else f"partial ({n_layers} layers)"
                        )

                        print(
                            f"🔄 Attempting GPU load "
                            f"[{label}] with {ctx_size} context..."
                        )

                        candidate = Llama(
                            model_path=str(self.target_path),
                            n_ctx=ctx_size,
                            n_threads=6,
                            n_batch=512,
                            type_k=self.type_k,
                            type_v=self.type_v,
                            n_gpu_layers=n_layers,
                            chat_format=self.active_config["chat_format"],
                            flash_attn=True,
                            verbose=False,
                        )

                        self.llm = candidate
                        candidate = None

                        self.CONTEXT_WINDOW = ctx_size

                        print(
                            f"🚀 Loaded on GPU [{label}] "
                            f"(Context: {self.CONTEXT_WINDOW}; KV quantization type: {self.type_k}, {self.type_v})."
                        )

                        return

                    except Exception as e:
                        print(
                            f"⚠️ GPU load failed "
                            f"[{label}] at {ctx_size} context: {e}"
                        )

                        # If construction returned an object before a later
                        # operation failed, close it explicitly.
                        if candidate is not None:
                            try:
                                close = getattr(candidate, "close", None)
                                if callable(close):
                                    close()
                            except Exception:
                                pass

                            del candidate

                        # Make sure Python-side native owners are collected
                        # before the next GPU allocation attempt.
                        gc.collect()

        # ------------------------------------------------------------------
        # CPU path
        #
        # IMPORTANT:
        # We still support CPU fallback for normal operation, but the caller
        # can distinguish this by inspecting whether GPU loading succeeded.
        # ------------------------------------------------------------------

        print(
            f"🐢 Running on CPU "
            f"(Detected System RAM: {total_ram:.1f} GB)..."
        )

        for ctx_size in cpu_contexts:
            candidate = None

            try:
                print(
                    f"🔄 Attempting CPU load "
                    f"with {ctx_size} context..."
                )

                candidate = Llama(
                    model_path=str(self.target_path),
                    n_ctx=ctx_size,
                    n_threads=6,
                    n_batch=512,
                    type_k=None,
                    type_v=None,
                    n_gpu_layers=0,
                    chat_format=self.active_config["chat_format"],
                    verbose=False,
                )

                self.llm = candidate
                candidate = None

                self.CONTEXT_WINDOW = ctx_size

                print(
                    f"🐢 Loaded on CPU "
                    f"(Context: {self.CONTEXT_WINDOW})."
                )

                return

            except Exception as e_cpu:
                print(
                    f"⚠️ CPU allocation failed at "
                    f"{ctx_size} context: {e_cpu}"
                )

                if candidate is not None:
                    try:
                        close = getattr(candidate, "close", None)
                        if callable(close):
                            close()
                    except Exception:
                        pass

                    del candidate

                gc.collect()

        raise RuntimeError(
            "Unable to initialize model on either GPU or CPU."
        )

    def __del__(self):
        """
        Best-effort emergency cleanup.

        Normal code should call close() explicitly. __del__ exists only as
        a last line of defense and intentionally suppresses all exceptions.
        """
        try:
            self.close()
        except Exception:
            pass
