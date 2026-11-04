"""Convert the Goedel weights with the named recipes, or all of them. Finished recipes are skipped.

Each conversion goes to a temporary folder first, so a killed run can't leave a half-written
model that looks complete. A failure is logged and the next recipe still runs.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from bench import ROOT, commit_free_gb, wait_for_memory, watch

SOURCE = ROOT / "models" / "Goedel-Prover-V2-8B-hf"
CLI = Path(sys.executable).parent / "optimum-cli.exe"
INT4_SYM = ["--weight-format", "int4", "--sym", "--ratio", "1.0"]

RECIPES = {
    # name: (arguments, time limit in seconds)
    "fp16": (["--weight-format", "fp16"], 1800),                       # accuracy reference
    "int4-cw": (INT4_SYM + ["--group-size", "-1"], 1800),              # the first benchmark's recipe
    "int4-cw-i8sym": (INT4_SYM + ["--group-size", "-1", "--backup-precision", "int8_sym"], 1800),
    "int4-gq128": (INT4_SYM + ["--group-size", "128"], 1800),
    "nf4-cw": (["--weight-format", "nf4", "--sym", "--ratio", "1.0", "--group-size", "-1",
                "--backup-precision", "int8_sym"], 1800),              # docs: Lunar Lake and later
    "int4-asym-gq128": (["--weight-format", "int4", "--ratio", "1.0", "--group-size", "128"], 1800),
    "int4-cw-awq-se": (INT4_SYM + ["--group-size", "-1", "--awq", "--scale-estimation",
                                   "--dataset", "wikitext2", "--num-samples", "64"], 5400),
    "int4-cw-awq": (INT4_SYM + ["--group-size", "-1", "--awq",        # lighter fallback
                                "--dataset", "wikitext2", "--num-samples", "64"], 5400),
}

for name in sys.argv[1:] or RECIPES:
    arguments, limit = RECIPES[name]
    target = ROOT / "models" / f"goedel-ov-{name}"
    if target.exists():
        print(f"CONVERT_SKIP {name}", flush=True)
        continue
    temporary = target.with_name(target.name + ".tmp")
    shutil.rmtree(temporary, ignore_errors=True)
    wait_for_memory()
    print(f"CONVERT_START {name} commit free {commit_free_gb():.1f} GB", flush=True)
    start = time.time()
    command = [str(CLI), "export", "openvino", "-m", str(SOURCE),
               "--task", "text-generation-with-past", *arguments, str(temporary)]
    # The exporter writes a full-size intermediate to TEMP and a killed run would leave it there.
    scratch = target.with_name(target.name + ".scratch")
    scratch.mkdir(exist_ok=True)
    environment = {**os.environ, "TEMP": str(scratch), "TMP": str(scratch)}
    code = watch(subprocess.Popen(command, env=environment), limit)
    shutil.rmtree(scratch, ignore_errors=True)
    if code == 0:
        temporary.rename(target)
        size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) / 2**30
        print(f"CONVERT_OK {name} {(time.time() - start) / 60:.1f} min {size:.2f} GiB", flush=True)
    else:
        shutil.rmtree(temporary, ignore_errors=True)
        print(f"CONVERT_FAILED {name} {code} after {(time.time() - start) / 60:.1f} min", flush=True)
print("CONVERT_ALL_DONE", flush=True)
