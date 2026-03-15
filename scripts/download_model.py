from huggingface_hub import hf_hub_download
import os

model_id = "bartowski/Llama-3.2-3B-Instruct-GGUF"
filename = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
dest_dir = "models"

if not os.path.exists(dest_dir):
    os.makedirs(dest_dir)

print(f"Downloading {filename} from {model_id}...")
path = hf_hub_download(repo_id=model_id, filename=filename, local_dir=dest_dir)
print(f"Model downloaded to: {path}")
