"""Rebuild results/summary.json from the raw logs, and print the tables used in the reports.

Standard library only. Run from anywhere:  python tools/make_summary.py
"""
import bisect
import collections
import datetime
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "goedel_test" / "bench" / "lab"
OUTPUT = ROOT / "results" / "summary.json"
POWER_KEY = "rapl_package0_pkg"
PWH_PER_JOULE = 2.78e8  # the counter's unit, checked against its own power reading
BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))
# Stopped at 15:32:27; the process restarted itself at this time and stayed on.
INDEXER_BACK = datetime.datetime(2026, 11, 4, 16, 20, 57, tzinfo=BRASILIA).timestamp()
AFTER_DRIVER_UPDATE = {"iGPU-awq-se", "NPU-awq-se", "NPU-int4-cw-driver5540"}

STAGES = {
    "stage1": ("old", "not recorded", "Recipes on every device, 256-token decode"),
    "probe": ("old", "not recorded", "Short tests of NPU options and built-in hybrids"),
    "probe2": ("old", "not recorded", "Short tests of more NPU options and hybrids"),
    "probe3": ("old", "not recorded", "First custom hybrid test"),
    "probe3b": ("old", "not recorded", "Custom hybrid test"),
    "probe3c": ("old", "not recorded", "Custom hybrid test"),
    "stage3": ("old", "not recorded", "Main comparison: 12 setups, 2 rounds"),
    "stage4": ("old", "not recorded", "Very long prompts (2926 tokens)"),
    "driver5540": ("new", "busy", "Interrupted run, timings possibly disturbed: do not use"),
    "driver5540-rerun": ("new", "busy", "NPU retest on the new driver, indexer using a full core"),
    "awq": ("new", "stopped", "AWQ against int4-cw, indexer stopped"),
}


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


RUNS = load(LAB / "runs.jsonl")
PROOFS = load(LAB / "prove.jsonl")
ACCURACY = load(LAB / "accuracy.jsonl")
SAMPLES = load(LAB / "telemetry.jsonl")
TIMES = [sample["t"] for sample in SAMPLES]


def energy(start, end):
    """Joules and mean watts from the cumulative package counter; (None, None) if no samples."""
    first, last = bisect.bisect_right(TIMES, start), bisect.bisect_left(TIMES, end) - 1
    if last <= first:
        return None, None
    joules = (SAMPLES[last]["energy_pwh"][POWER_KEY] - SAMPLES[first]["energy_pwh"][POWER_KEY]) / PWH_PER_JOULE
    return joules, joules / (TIMES[last] - TIMES[first])


def median(values, digits=2):
    values = [value for value in values if value is not None]
    return round(st.median(values), digits) if values else None


def stage_tables():
    grouped = collections.OrderedDict()
    for run in RUNS:
        grouped.setdefault(run["stage"], collections.OrderedDict()).setdefault(run["config"], []).append(run)
    tables = {}
    for stage, configs in grouped.items():
        driver, indexer, description = STAGES[stage]
        idle = [energy(r["start"], r["end"])[1] for rows in configs.values() for r in rows if r["phase"] == "idle"]
        table = {}
        for name, rows in configs.items():
            pick = lambda phase: [r for r in rows if r["phase"] == phase]
            decode = [r["decode_tok_s"] for r in pick("short") if r.get("decode_tok_s")]

            def per_token(phase):
                result = []
                for run in pick(phase):
                    joules = energy(run["start"], run["end"])[0]
                    if joules is not None and run["generated"]:
                        result.append(joules / run["generated"])
                return median(result)

            table[name] = {
                "decode_tok_s": median(decode),
                "decode_spread": round(max(decode) - min(decode), 2) if decode else None,
                "decode_by_round": {
                    str(index): median([r["decode_tok_s"] for r in pick("short") if r["round"] == index])
                    for index in sorted({r["round"] for r in pick("short")})},
                "deep_tok_s": median([r.get("decode_tok_s") for r in pick("deep")]),
                "ttft_short_s": median([r.get("ttft_s") for r in pick("short")]),
                "ttft_long_s": median([r.get("ttft_s") for r in pick("long")]),
                "ttft_xlong_s": median([r.get("ttft_s") for r in pick("xlong")]),
                "xlong_decode_tok_s": median([r.get("decode_tok_s") for r in pick("xlong")]),
                "package_w": median([energy(r["start"], r["end"])[1] for r in pick("short")], 1),
                "j_per_token_short": per_token("short"),
                "j_per_token_deep": per_token("deep"),
                "load_s": median([r.get("load_s") for r in pick("load")]),
                "peak_ram_gb": median([r.get("worker_peak_gb") for r in pick("memory")]),
                "failed_sessions": len(pick("failed")),
            }
        tables[stage] = {
            "npu_driver": driver, "search_indexer": indexer, "description": description,
            "idle_w_median": median(idle, 1), "configs": table,
        }
    return tables


def proof_tables():
    grouped = collections.OrderedDict()
    for row in PROOFS:
        grouped.setdefault(row["config"], []).append(row)
    tables = {}
    for name, rows in grouped.items():
        joules = [energy(r["start"], r["end"])[0] for r in rows]
        proved = sum(r["verdict"] == "PROVED" for r in rows)
        minutes = sum(r["total_s"] for r in rows) / 60
        kilojoules = sum(j for j in joules if j) / 1000
        affected = [r["lemma"] for r in rows if name in AFTER_DRIVER_UPDATE and r["end"] > INDEXER_BACK]
        tables[name] = {
            "proved": proved, "attempted": len(rows), "minutes": round(minutes, 1),
            "kilojoules": round(kilojoules, 1),
            "minutes_per_proof": round(minutes / proved, 1) if proved else None,
            "kilojoules_per_proof": round(kilojoules / proved, 1) if proved else None,
            "generated_tokens": sum(r["generated"] for r in rows),
            "prefill_share_of_time": round(sum(r["ttft_s"] or 0 for r in rows) / sum(r["total_s"] for r in rows), 4),
            "npu_driver": "new" if name in AFTER_DRIVER_UPDATE else "old",
            "indexer_on_for_tasks": affected,
            "tasks": {r["lemma"]: {"verdict": r["verdict"], "generated": r["generated"],
                                    "decode_tok_s": r["decode_tok_s"], "total_s": r["total_s"],
                                    "watts": round(energy(r["start"], r["end"])[1], 1)} for r in rows},
        }
    return tables


def accuracy_table():
    grouped = collections.OrderedDict()
    for row in ACCURACY:
        grouped.setdefault((row["ir"], row["device"]), []).append(row)
    table = []
    for (recipe, device), rows in grouped.items():
        positions = sum(r["positions"] for r in rows)
        mean = lambda key: round(sum(r[key] * r["positions"] for r in rows) / positions * 100, 2)
        table.append({"recipe": recipe, "device": device, "positions": positions, "top1_pct": mean("top1"),
                      "top1_confident_pct": mean("top1_confident"), "top5_overlap_pct": mean("top5_overlap")})
    return sorted(table, key=lambda row: -row["top1_pct"])


def token_checks():
    """Do repeated runs give the same tokens, and which setups' outputs match each other?"""
    hashes = collections.defaultdict(set)
    for run in RUNS:
        if run.get("token_sha"):
            hashes[(run["stage"], run["config"], run["phase"])].add(run["token_sha"])
    repeats = [key for key, values in hashes.items() if len(values) > 1 and key[0] != "driver5540"]
    lookup = lambda stage, config, phase: sorted(hashes.get((stage, config, phase), []))
    old = {r["lemma"]: r for r in PROOFS if r["config"] == "NPU-chunk128"}
    new = {r["lemma"]: r for r in PROOFS if r["config"] == "NPU-int4-cw-driver5540"}
    return {
        "setups_whose_repeats_differ": ["/".join(key) for key in repeats],
        "npu_chunk128_short_same_across_drivers":
            lookup("stage3", "NPU-chunk128", "short") == lookup("awq", "NPU-int4-cw", "short"),
        "npu_chunk128_deep_same_across_drivers":
            lookup("stage3", "NPU-chunk128", "deep") == lookup("awq", "NPU-int4-cw", "deep"),
        "hybrid_short_same_as_cpu":
            lookup("stage3", "HYB-NPU128-CPU", "short") == lookup("stage3", "CPU-native", "short"),
        "hybrid_igpu_short_same_as_cpu":
            lookup("stage3", "HYB-NPU128-iGPU", "short") == lookup("stage3", "CPU-native", "short"),
        "proof_outputs_changed_by_driver": sorted(
            name for name in new if old[name]["token_sha"] != new[name]["token_sha"]),
    }


def show_proofs(tables):
    print("\n## proofs\n")
    print("setup | proved | min | kJ | min/proof | kJ/proof | prefill share | indexer on for")
    for name, row in tables.items():
        print(f"{name} | {row['proved']}/{row['attempted']} | {row['minutes']} | {row['kilojoules']} | "
              f"{row['minutes_per_proof']} | {row['kilojoules_per_proof']} | "
              f"{row['prefill_share_of_time'] * 100:.2f}% | {len(row['indexer_on_for_tasks'])}")


def show_stages(tables):
    columns = ["decode_tok_s", "decode_spread", "deep_tok_s", "ttft_short_s", "ttft_long_s", "ttft_xlong_s",
               "xlong_decode_tok_s", "package_w", "j_per_token_short", "j_per_token_deep", "load_s",
               "peak_ram_gb", "failed_sessions"]
    for stage, table in tables.items():
        print(f"\n## {stage}: driver {table['npu_driver']}, indexer {table['search_indexer']}, "
              f"idle {table['idle_w_median']} W\n")
        print("setup | " + " | ".join(columns))
        for name, row in table["configs"].items():
            print(name + " | " + " | ".join(str(row[column]) for column in columns))


def main():
    stages, proofs = stage_tables(), proof_tables()
    summary = {
        "generated_by": "tools/make_summary.py",
        "units": {"decode_tok_s": "tokens per second after the first token", "ttft": "seconds to first token",
                  "package_w": "CPU package watts including idle", "energy": "RAPL package counter only"},
        "indexer_back_unix": INDEXER_BACK,
        "stages": stages, "proofs": proofs, "accuracy": accuracy_table(), "token_checks": token_checks(),
    }
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    show_stages(stages)
    show_proofs(proofs)
    print("\n## accuracy\n")
    for row in summary["accuracy"]:
        print(row)
    print("\n## token checks\n")
    print(json.dumps(summary["token_checks"], indent=1))


if __name__ == "__main__":
    main()
