import os
import requests
from tqdm import tqdm
from pathlib import Path
import argparse


models = {
    "codellama-13b": {
        "repo_id": "TheBloke/CodeLlama-13B-Instruct-GGUF",
        "filename": "codellama-13b-instruct.Q3_K_S.gguf"
    },
    "deepseek-r1-distill-qwen-7b": {
        "repo_id": "bartowski/DeepSeek-R1-Distill-Qwen-7B-GGUF",
        "filename": "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf"
    },
    "hermes3": {
        "repo_id": "NousResearch/Hermes-3-Llama-3.1-8B-GGUF",
        "filename": "Hermes-3-Llama-3.1-8B.Q4_K_M.gguf"
    },
    "mistral-nemo-12b": {
        "repo_id": "bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        "filename": "Mistral-Nemo-Instruct-2407-Q3_K_S.gguf"
    },
    "mistral-nemo-12b-q2k": {
        "repo_id": "bartowski/Mistral-Nemo-Instruct-2407-GGUF",
        "filename": "Mistral-Nemo-Instruct-2407-Q2_K.gguf"
    },
    "phi-4-14b": {
        "repo_id": "bartowski/phi-4-GGUF",
        "filename": "phi-4-Q3_K_S.gguf"
    },
    "phi-4-14b-q2j": {
        "repo_id": "bartowski/phi-4-GGUF",
        "filename": "phi-4-Q2_K.gguf"
    },
    "qwen-2.5-coder-14b": {
        "repo_id": "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-14B-Instruct-Q3_K_S.gguf"
    },
    "qwen-2.5-coder-14b-q2k": {
        "repo_id": "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-14B-Instruct-Q2_K.gguf"
    },
    "qwen-2.5-coder-7b": {
        "repo_id": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    },
    "qwen-2.5-coder-7b-q5-k-m": {
        "repo_id": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf"
    },
    "qwen-2.5-coder-7b-q6-k": {
        "repo_id": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-7B-Instruct-Q6_K.gguf"
    },
    "qwen-3.5-9b": {
        "repo_id": "bartowski/Qwen3.5-9B-Instruct-GGUF",
        "filename": "Qwen3.5-9B-Instruct-Q4_K_M.gguf"
    },
    "qwen-3-8b": {
        "repo_id": "bartowski/Qwen3-8B-Instruct-GGUF",
        "filename": "Qwen3-8B-Instruct-Q4_K_M.gguf"
    },
    "starcoder": {
        "repo_id": "QuantFactory/starcoder2-7b-instruct-GGUF",
        "filename": "starcoder2-7b-instruct.Q4_K_M.gguf"
    }
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
    parser = argparse.ArgumentParser(description="Download a model by ID.")
    parser.add_argument("--model", type=str, required=True, help="Model ID to download")
    args = parser.parse_args()
    download_model(args.model)
