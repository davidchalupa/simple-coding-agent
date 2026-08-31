import os
import requests
from tqdm import tqdm
from pathlib import Path

# Global variable for models
models = {
    "codellama-13b": {
        "repo_id": "TheBloke/CodeLlama-13B-Instruct-GGUF",
        "filename": "codellama-13b-instruct.Q3_K_S.gguf"
    },
    "deepseek-r1-distill-qwen-7b": {
        "repo_id": "bartowski/DeepSeek-R1-Distill-Qwen-7B-GGUF",
        "filename": "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf"
    },
    # Add more models as needed
}

def download_model(model_id):
    model_info = models.get(model_id)
    if not model_info:
        print(f"Model {model_id} not found.")
        return

    repo_id = model_info["repo_id"]
    filename = model_info["filename"]
    dest_dir = Path(__file__).resolve().parent.parent / "models"

    os.makedirs(dest_dir, exist_ok=True)

    url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    tmp_path = os.path.join(dest_dir, filename + ".part")
    out_path = os.path.join(dest_dir, filename)

    print(f"Starting download: {filename}...")

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        block_size = 1024  # 1 Kibibyte
        t = tqdm(total=total_size, unit='iB', unit_scale=True)

        with open(tmp_path, 'wb') as f:
            for data in response.iter_content(block_size):
                t.update(len(data))
                f.write(data)
        t.close()

        if total_size != 0 and t.n != total_size:
            print("ERROR, something went wrong")
        else:
            os.rename(tmp_path, out_path)
            print(f"Download completed: {filename}")
    except requests.exceptions.RequestException as e:
        print(f"Error downloading {filename}: {e}")

if __name__ == "__main__":
    model_id = input("Enter the model ID to download: ")
    download_model(model_id)
