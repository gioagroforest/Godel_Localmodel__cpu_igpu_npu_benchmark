"""Download OpenVINO's official NPU-optimized Qwen3-8B, used as a recipe and speed reference."""
from pathlib import Path

from huggingface_hub import snapshot_download

TARGET = Path(__file__).resolve().parents[2] / "models" / "Qwen3-8B-int4-cw-ov-official"

path = snapshot_download("OpenVINO/Qwen3-8B-int4-cw-ov", local_dir=TARGET)
size = sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())
print(f"REFERENCE_OK {path} {size / 2**30:.1f} GiB")
