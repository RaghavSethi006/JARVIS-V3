"""
Downloads Kokoro ONNX model files.
Run: python scripts/download_kokoro.py
"""

import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "models", "kokoro")
BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files"
FILES = ["kokoro-v0_19.onnx", "voices.bin"]

os.makedirs(DEST, exist_ok=True)

for fname in FILES:
    dest_path = os.path.join(DEST, fname)
    if os.path.exists(dest_path):
        print(f"  Already exists: {fname}")
        continue
    url = f"{BASE_URL}/{fname}"
    print(f"  Downloading {fname} ...")
    urllib.request.urlretrieve(url, dest_path)
    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
    print(f"  Done - {size_mb:.1f} MB")

print("\nKokoro models ready. Restart Jarvis.")
