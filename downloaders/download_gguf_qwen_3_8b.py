import os
import requests
from tqdm import tqdm
from pathlib import Path

# Qwen 3 8B (GGUF) - The best balance for 8GB VRAM
repo_id = "bartowski/DeepSeek-R1-Distill-Qwen-7B-GGUF"
filename = "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf"
dest_dir = Path(__file__).resolve().parent.parent / "models"

os.makedirs(dest_dir, exist_ok=True)

url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
tmp_path = os.path.join(dest_dir, filename + ".part")
out_path = os.path.join(dest_dir, filename)

print(f"Starting download: {filename}...")
print(f"This is approximately 5.2GB. Grab a coffee!")

try:
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))

        with open(tmp_path, "wb") as f, tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=filename
        ) as pbar:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk))

    os.replace(tmp_path, out_path)
    print("\n✅ Success! Model saved to:", out_path)

except Exception as e:
    print(f"\n❌ Download failed: {e}")
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
