import gc
import time

from pathlib import Path

from common.llm_init import LLMInitializer

from model_registry import MODEL_REGISTRY


class ModelSwitcher:
    """
    Owns at most one Llama model at a time.

    Model lifetime is explicit:
        current model
            -> close()
            -> GC
            -> new model

    We intentionally do NOT call llama_backend_init() / llama_backend_free().
    Those are process/backend lifecycle operations, not model-switch reset
    operations.
    """

    def __init__(self, kv_quantization_type, models_dir: Path):
        self.kv_quantization_type = kv_quantization_type
        self.models_dir = models_dir

        # === NEW: Cache directory and tracking ===
        self.cache_dir = None  # Will be initialized in _ensure_cache_dir_exists()

        self.current_key = None
        self.initializer = None
        self.llm = None
        self.context_window = None
        self.display_name = None

    def _unload(self):
        if self.initializer is None and self.llm is None:
            return

        if self.display_name:
            print(f"🗑️  Unloading {self.display_name}...")

        initializer = self.initializer

        # Clear switcher references first so there is no accidental
        # retention through this object while cleanup runs.
        self.initializer = None
        self.llm = None
        self.context_window = None
        self.display_name = None
        self.current_key = None

        if initializer is not None:
            try:
                initializer.close()
            except Exception as e:
                print(
                    f"⚠️ [ModelSwitcher] "
                    f"Initializer cleanup failed: {e}"
                )

            del initializer

        # Give Python a chance to release any other objects created by
        # model/chat initialization.
        gc.collect()

        # A small delay gives CUDA/native teardown some breathing room.
        time.sleep(0.5)

    def ensure_cache_dir_exists(self):
        """Create cache directory if it doesn't exist."""
        import os

        self.cache_dir = Path.home() / ".coding_consultant" / "cache"

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

            # Check write permissions
            test_file = self.cache_dir / ".write_test"
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)

        except Exception as e:
            print(f"\n⚠️ [KV Cache] Cannot create cache directory {self.cache_dir}: {e}")
            self.cache_dir = None

    def get_cache_filename(self, model_key):
        """Generate a timestamped filename for KV cache."""
        import time

        if not self.cache_dir:
            return None

        # Use current timestamp to allow multiple sessions per model
        timestamp = int(time.time())

        safe_model_name = model_key.replace('-', '_').replace('.', '_')
        filename = f"{safe_model_name}_{timestamp}.bin"

        return os.path.join(self.cache_dir, filename)

    def save_current_context(self, cache_file_path):
        """Save current KV cache to file."""
        if not self.initializer:
            print("⚠️ [KV Cache] No model loaded. Cannot save.")
            return False

        return self.initializer.save_kv_cache(cache_file_path)

    def restore_previous_context(self, cache_file_path):
        """Restore KV cache from file."""
        if not self.initializer:
            print("⚠️ [KV Cache] No model loaded. Cannot restore.")
            return False

        return self.initializer.load_kv_cache(cache_file_path)

    def load(self, model_key: str):
        if (
            self.current_key == model_key
            and self.llm is not None
        ):
            return self.llm, self.context_window

        # Different model: fully close current model first.
        self._unload()

        config = MODEL_REGISTRY[model_key]
        target_path = self.models_dir / config["filename"]
        display_name = config["display_name"]

        initializer = LLMInitializer(
            target_path,
            display_name,
            config,
            self.kv_quantization_type,
        )

        try:
            initializer.initialize_agent()

        except Exception:
            # Make absolutely sure a partially initialized candidate is
            # released before propagating the error.
            try:
                initializer.close()
            except Exception:
                pass

            del initializer
            gc.collect()

            raise

        self.current_key = model_key
        self.initializer = initializer
        self.llm = initializer.llm
        self.context_window = initializer.CONTEXT_WINDOW
        self.display_name = display_name

        return self.llm, self.context_window

    def unload_current(self):
        """
        Fully releases the current model before another process is started,
        e.g. the /diagnose reasoning worker.
        """
        self._unload()
