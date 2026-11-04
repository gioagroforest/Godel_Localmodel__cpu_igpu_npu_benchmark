"""Real proofs on the chosen setups, one guarded process per setup."""
import json
import subprocess
import sys
from pathlib import Path

import bench

MATRIX = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "lab_prove.json"
spec = json.loads(MATRIX.read_text(encoding="utf-8"))
for config in spec["configs"]:
    bench.wait_for_memory()
    command = [sys.executable, str(Path(__file__).parent / "bench.py"), "prove", "--matrix", str(MATRIX),
               "--only", config["name"], "--max-tokens", str(spec["max_tokens"]),
               "--lemmas", ",".join(config.get("lemmas", spec["lemmas"]))]
    code = bench.watch(subprocess.Popen(command), spec["timeout"], 5.0)
    print(f"PROVE_SETUP {config['name']}: {code}", flush=True)
print("LAB_PROVE_DONE", flush=True)
