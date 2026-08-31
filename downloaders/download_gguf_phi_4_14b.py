import os
import requests
from tqdm import tqdm
from pathlib import Path

# Microsoft Phi-4 14B - Q3_K_S (~6.50 GB)
# Fits 100% in 8GB VRAM with context headroom
repo_id = "bartowski/phi-4-GGUF"
filename = "phi-4-Q3_K_S.gguf"
dest_dir = Path(__file__).resolve().parent.parent / "models"

os.makedirs(dest_dir, exist_ok=True)

url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
tmp_path = os.path.join(dest_dir, filename + ".part")
out_path = os.path.join(dest_dir, filename)

print(f"Starting download: {filename}...")
print("File size is approximately 6.50 GB.")

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
            for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk))

    os.replace(tmp_path, out_path)
    print("\n✅ Success! Model saved to:", out_path)

except Exception as e:
    print(f"\n❌ Download failed: {e}")
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
