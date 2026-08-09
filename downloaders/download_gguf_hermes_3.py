import os
import requests
from tqdm import tqdm
from pathlib import Path

repo_id = "NousResearch/Hermes-3-Llama-3.1-8B-GGUF"
filename = "Hermes-3-Llama-3.1-8B.Q4_K_M.gguf"
dest_dir = Path(__file__).resolve().parent.parent / "models"

os.makedirs(dest_dir, exist_ok=True)
url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
out_path = os.path.join(dest_dir, filename)

print(f"🚀 Downloading Hermes-3-Llama-3.1-8B (Agent Alternative)...")

try:
    headers = {"User-Agent": "Mozilla/5.0"}
    with requests.get(url, stream=True, timeout=120, headers=headers) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))

        with open(out_path, "wb") as f, tqdm(
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

    print(f"\n✅ Success! Saved at: {out_path}")

except Exception as e:
    print(f"\n❌ Download failed: {e}")
