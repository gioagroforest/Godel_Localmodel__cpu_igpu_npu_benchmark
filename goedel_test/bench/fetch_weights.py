"""Download the original Goedel-Prover-V2-8B weights, without the repo's helper scripts."""
from pathlib import Path

from huggingface_hub import snapshot_download

TARGET = Path(__file__).resolve().parents[2] / "models" / "Goedel-Prover-V2-8B-hf"

path = snapshot_download(
    "Goedel-LM/Goedel-Prover-V2-8B",
    local_dir=TARGET,
    allow_patterns=["*.safetensors", "*.json", "merges.txt"],
)
size = sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())
print(f"DOWNLOAD_OK {path} {size / 2**30:.1f} GiB")
