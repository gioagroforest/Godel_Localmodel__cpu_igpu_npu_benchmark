"""One table per lab stage: median speed, package power and energy, with session spread."""
import argparse
import bisect
import collections
import json
import statistics as st
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--dir", default=str(Path(__file__).parent / "lab"))
parser.add_argument("--stage")
args = parser.parse_args()
folder = Path(args.dir)
runs = [json.loads(l) for l in (folder / "runs.jsonl").open(encoding="utf-8")]
samples = [json.loads(l) for l in (folder / "telemetry.jsonl").open(encoding="utf-8")]
times = [s["t"] for s in samples]


KEY = "rapl_package0_pkg"


def joules(start, end):
    """Energy from the cumulative counter (picowatt-hours); None if the window has no samples."""
    i, j = bisect.bisect_right(times, start), bisect.bisect_left(times, end) - 1
    if j <= i:
        return None, None
    used = (samples[j]["energy_pwh"][KEY] - samples[i]["energy_pwh"][KEY]) / 2.78e8
    return used, used / (times[j] - times[i])


def median(values):
    values = [v for v in values if v is not None]
    return round(st.median(values), 2) if values else None


stages = collections.OrderedDict()
for run in runs:
    stages.setdefault(run["stage"], collections.OrderedDict()).setdefault(run["config"], []).append(run)
for stage, configs in stages.items():
    if args.stage and stage != args.stage:
        continue
    print(f"\n## {stage}\n")
    print("| config | decode tok/s | spread | deep tok/s | TTFT 229 | TTFT 974 | TTFT xlong | W | J/token | load s | peak GB | failed |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, rows in configs.items():
        short = [r for r in rows if r["phase"] == "short"]
        decode = [r["decode_tok_s"] for r in short if r.get("decode_tok_s")]
        energy = [joules(r["start"], r["end"]) for r in short]
        per_token = [e[0] / r["generated"] for e, r in zip(energy, short) if e[0] is not None]
        ttft = lambda phase: median([r.get("ttft_s") for r in rows if r["phase"] == phase])
        failed = [str(r.get("code")) for r in rows if r["phase"] == "failed"]
        print(f"| {name} | {median(decode)} | {round(max(decode) - min(decode), 2) if decode else None} "
              f"| {median([r.get('decode_tok_s') for r in rows if r['phase'] == 'deep'])} "
              f"| {ttft('short')} | {ttft('long')} | {ttft('xlong')} | {median([e[1] for e in energy])} "
              f"| {median(per_token)} | {median([r.get('load_s') for r in rows if r['phase'] == 'load'])} "
              f"| {median([r.get('worker_peak_gb') for r in rows if r['phase'] == 'memory'])} | {','.join(failed)} |")
