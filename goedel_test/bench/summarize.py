"""Summarize the Goedel-Prover-V2-8B benchmark written by bench.py.

Reads out/runs.jsonl and out/telemetry.jsonl (and out/prove.jsonl if present) and prints a
markdown report, which is also written to out/report.md. Standard library only.

Rules used everywhere: a value that is missing or null is shown as n/a, never filled in.
Ranges are written "median (min..max)". Only stage=main feeds the main, energy and
device tables; sweeps get their own table.
"""
import argparse
import bisect
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

KNOWN_CONFIGS = ["A-OV-CPU", "A-OV-iGPU", "A-OV-NPU", "B-llama-CPU", "B-llama-iGPU"]
PACKAGE_KEY = "rapl_package0_pkg"
MIN_SAMPLES = 3         # a power window needs at least this many samples
MAX_GAP_S = 5.0         # larger holes between power samples invalidate a window
MIN_COVERAGE = 0.95     # fraction of the window the power samples must span
DRIFT_LIMIT = 0.05      # rounds whose median decode rates differ more than this are flagged
NA = "n/a"


# ---------------------------------------------------------------- small helpers
def num(value):
    """A finite float, or None. Booleans, strings and NaN do not count as numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def nums(values):
    return [v for v in map(num, values) if v is not None]


def fmt(value, digits=1):
    value = num(value)
    return NA if value is None else f"{value:.{digits}f}"


def med_range(values, digits=1, show_n=True):
    """'median (min..max) n=K' over the numeric values, or n/a when there are none."""
    values = nums(values)
    if not values:
        return NA
    text = f"{statistics.median(values):.{digits}f} ({min(values):.{digits}f}..{max(values):.{digits}f})"
    return f"{text} n={len(values)}" if show_n else text


def mean_or_none(values):
    values = nums(values)
    return statistics.fmean(values) if values else None


def table(headers, rows):
    def cell(x):
        return str(x).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(cell(h) for h in headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return lines + [""]


def read_jsonl(path):
    """(records, skipped_line_count). A missing file gives (None, 0). Bad lines are skipped."""
    path = Path(path)
    if not path.exists():
        return None, 0
    records, skipped = [], 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1     # typically a half-written last line
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                skipped += 1
    return records, skipped


def ordered(names):
    """Known configs in the standard order first, then any others in first-seen order."""
    names = list(dict.fromkeys(names))
    known = [n for n in KNOWN_CONFIGS if n in names]
    return known + [n for n in names if n not in KNOWN_CONFIGS]


def of(runs, **conditions):
    return [r for r in runs if all(r.get(k) == v for k, v in conditions.items())]


def config_names(runs):
    return ordered(r["config"] for r in runs if isinstance(r.get("config"), str))


def round_key(record):
    value = num(record.get("round"))
    return math.inf if value is None else value


def prompt_rate(record):
    """Prompt-processing rate: prompt tokens per second of time to first token."""
    tokens, ttft = num(record.get("prompt_tokens")), num(record.get("ttft_s"))
    if tokens is None or ttft is None or ttft <= 0:
        return None
    return tokens / ttft


# ---------------------------------------------------------------- telemetry
class Telemetry:
    def __init__(self, samples):
        usable = [s for s in samples if num(s.get("t")) is not None]
        self.samples = sorted(usable, key=lambda s: s["t"])
        self.times = [s["t"] for s in self.samples]

    def between(self, start, end):
        """Samples with start <= t <= end."""
        return self.samples[bisect.bisect_left(self.times, start):bisect.bisect_right(self.times, end)]


def package_watts(sample):
    power = sample.get("power_mw")
    if not isinstance(power, dict):
        return None
    if PACKAGE_KEY in power:
        milliwatts = num(power[PACKAGE_KEY])
    else:
        milliwatts = next((num(v) for k, v in power.items() if k.endswith("_pkg")), None)
    return None if milliwatts is None else milliwatts / 1000.0


def interpolate(p0, p1, t):
    (t0, w0), (t1, w1) = p0, p1
    return (t, w0 + (w1 - w0) * (t - t0) / (t1 - t0))


def package_energy(telemetry, start, end):
    """Integrate package power over [start, end] with the trapezoid rule.

    Uses the samples inside the window. When a sample just outside the window exists on a
    side (within MAX_GAP_S of the next one), the curve is interpolated to that window edge,
    so the second or so between the edge and the first sample is not lost.
    Returns ((joules, covered_seconds), None), or (None, reason) when the window is unusable.
    """
    start, end = num(start), num(end)
    if telemetry is None or start is None or end is None or end <= start:
        return None, "no window or telemetry"
    points = []
    for sample in telemetry.between(start - MAX_GAP_S, end + MAX_GAP_S):
        watts = package_watts(sample)
        if watts is not None:
            points.append((sample["t"], watts))
    inside = [p for p in points if start <= p[0] <= end]
    if len(inside) < MIN_SAMPLES:
        return None, f"fewer than {MIN_SAMPLES} samples"
    before = [p for p in points if p[0] < start]
    after = [p for p in points if p[0] > end]
    curve = list(inside)
    if before and inside[0][0] - before[-1][0] <= MAX_GAP_S:
        curve.insert(0, interpolate(before[-1], inside[0], start))
    if after and after[0][0] - inside[-1][0] <= MAX_GAP_S:
        curve.append(interpolate(inside[-1], after[0], end))
    pairs = list(zip(curve, curve[1:]))
    if any(b[0] - a[0] > MAX_GAP_S for a, b in pairs):
        return None, f"gap over {MAX_GAP_S:g} s"
    covered = curve[-1][0] - curve[0][0]
    if covered < MIN_COVERAGE * (end - start):
        return None, "window not covered"
    joules = sum(0.5 * (a[1] + b[1]) * (b[0] - a[0]) for a, b in pairs)
    return (joules, covered), None


# ---------------------------------------------------------------- 1. main table
def main_table(runs):
    lines = ["## 1. Main results (stage=main)", ""]
    main = of(runs, stage="main")
    if not main:
        return lines + ["No stage=main records.", ""]
    rows = []
    for config in config_names(main):
        mine = of(main, config=config)
        short, long_ = of(mine, phase="short"), of(mine, phase="long")
        loads = sorted((r for r in of(mine, phase="load") if num(r.get("load_s")) is not None),
                       key=round_key)       # sorted() is stable, so file order breaks ties
        first = f"{fmt(loads[0]['load_s'], 2)} (round {loads[0].get('round', NA)})" if loads else NA
        rows.append([
            config,
            med_range([r.get("decode_tok_s") for r in short], 2),
            med_range([r.get("ttft_s") for r in short], 3),
            med_range([prompt_rate(r) for r in long_], 1),
            first,
            med_range([r.get("load_s") for r in loads[1:]], 2),
        ])
    headers = ["config", "decode tok/s", "short-prompt TTFT s", "prompt tok/s (long)",
               "first load s", "later loads s"]
    lines += table(headers, rows)
    lines += ["Cells are median (min..max) n=samples, pooled over rounds and repetitions. "
              "Prompt rate = prompt_tokens / ttft_s on the long prompt (TTFT includes the first "
              "generated token). The first load is the one with the lowest round; it may be a cold "
              "compile, while later loads should come from the cache.", ""]
    return lines


# ---------------------------------------------------------------- 2. energy
def idle_baselines(runs, telemetry):
    """(stage, config, round) -> mean package watts of that session's idle record."""
    result = {}
    for record in of(runs, phase="idle"):
        found, _ = package_energy(telemetry, record.get("start"), record.get("end"))
        if found:
            joules, covered = found
            result[(record.get("stage"), record.get("config"), record.get("round"))] = joules / covered
    return result


def energy_table(runs, telemetry):
    lines = ["## 2. Energy (package power, short-prompt windows, stage=main)", ""]
    main = of(runs, stage="main")
    if telemetry is None:
        return lines + ["No telemetry file; energy is n/a.", ""]
    if not any(package_watts(s) is not None for s in telemetry.samples):
        return lines + [f"No `{PACKAGE_KEY}` power in the telemetry; energy is n/a.", ""]
    idle = idle_baselines(runs, telemetry)
    rows = []
    for config in config_names(main):
        watts, net_watts, gross_jpt, net_jpt, excluded = [], [], [], [], Counter()
        used = 0
        for run in of(main, config=config, phase="short"):
            found, reason = package_energy(telemetry, run.get("start"), run.get("end"))
            if not found:
                excluded[reason] += 1
                continue
            used += 1
            joules, covered = found
            base = idle.get(("main", config, run.get("round")))
            tokens = num(run.get("generated"))
            watts.append(joules / covered)
            if base is not None:
                net_watts.append(joules / covered - base)
            if tokens:
                gross_jpt.append(joules / tokens)
                if base is not None:
                    net_jpt.append((joules - base * covered) / tokens)
        base_mean = mean_or_none(v for k, v in idle.items() if k[0] == "main" and k[1] == config)
        why = ", ".join(f"{reason}: {count}" for reason, count in excluded.items())
        total_excluded = sum(excluded.values())
        rows.append([config, used, f"{total_excluded} ({why})" if why else 0,
                     med_range(watts, 1, False), med_range(net_watts, 1, False),
                     med_range(gross_jpt, 3, False), med_range(net_jpt, 3, False),
                     fmt(base_mean, 1)])
    headers = ["config", "windows used", "windows excluded", "package W", "net W",
               "J/token gross", "J/token net", "mean idle W"]
    lines += table(headers, rows)
    lines += ["Cells are median (min..max) over the used windows. Joules = trapezoid integral of "
              f"`{PACKAGE_KEY}` over the run's [start, end] (prefill included), interpolated to the "
              "window edges from the neighbouring samples when those exist. Net = minus the mean "
              "power of the idle record of the same config and round (J/token net subtracts "
              "idle W x window seconds). J/token divides by `generated`. Windows with fewer than "
              f"{MIN_SAMPLES} samples, a gap over {MAX_GAP_S:g} s, or under {MIN_COVERAGE:.0%} "
              "coverage are excluded. Net values are n/a when the session has no usable idle record.", ""]
    return lines


# ---------------------------------------------------------------- 3. device verification
def device_table(runs, telemetry):
    lines = ["## 3. Device verification (short-prompt windows, stage=main)", ""]
    main = of(runs, stage="main")
    if telemetry is None:
        return lines + ["No telemetry file; device usage is n/a.", ""]
    rows = []
    for config in config_names(main):
        samples = {}
        for run in of(main, config=config, phase="short"):
            start, end = num(run.get("start")), num(run.get("end"))
            if start is not None and end is not None:
                for sample in telemetry.between(start, end):
                    samples[sample["t"]] = sample
        samples = list(samples.values())
        totals = Counter()
        for sample in samples:
            engines = sample.get("engines")
            if isinstance(engines, dict):
                for key, value in engines.items():
                    if num(value) is not None:
                        totals[key] += value
        top = [f"{key} {total / len(samples):.1f}%" for key, total in totals.most_common(3)] if samples else []
        rows.append([config, len(samples),
                     fmt(mean_or_none(s.get("cpu_time") for s in samples), 1),
                     fmt(mean_or_none(s.get("cpu_mhz") for s in samples), 0),
                     "; ".join(top) if top else NA])
    lines += table(["config", "samples", "mean cpu_time %", "mean cpu_mhz", "top 3 engines (mean %)"], rows)
    lines += ["Engine keys are `<luid>:<engtype>`. The mean is over all samples in the windows; "
              "a sample where the key was omitted (under 0.5%) counts as 0.", ""]
    return lines


# ---------------------------------------------------------------- 4. consistency checks
def check(ok, text):
    return f"- {'PASS' if ok else 'WARN'}: {text}"


def values_agree(runs, field):
    """Check that `field` has one value per (stage, phase) across all configs."""
    lines = []
    for stage in sorted({str(r.get("stage")) for r in runs}):
        for phase in ("short", "long"):
            group = [r for r in runs if str(r.get("stage")) == stage and r.get("phase") == phase
                     and r.get(field) is not None]
            if not group:
                continue
            per_config = {c: sorted({r[field] for r in group if r["config"] == c}, key=str)
                          for c in config_names(group)}
            distinct = {v for values in per_config.values() for v in values}
            label = f"{field} across configs ({stage}/{phase})"
            if len(distinct) == 1:
                lines.append(check(True, f"{label}: all {len(group)} runs = {next(iter(distinct))}"))
            else:
                detail = "; ".join(f"{c}={v}" for c, v in per_config.items())
                lines.append(check(False, f"{label} differ: {detail}"))
    return lines or [f"- n/a: {field} across configs (no data)"]


def finish_reasons(runs):
    bad = Counter()
    for r in runs:
        if r.get("phase") in ("short", "long") and r.get("finish") not in (None, "length"):
            bad[(r.get("stage"), r["config"], r.get("finish"))] += 1
    if not bad:
        return [check(True, "every short/long run finished with finish=length")]
    detail = "; ".join(f"{c} ({s}) finish={f} x{n}" for (s, c, f), n in bad.items())
    return [check(False, f"runs that did not hit the token limit (ignore_eos was on): {detail}")]


def sha_checks(runs):
    timed = [r for r in runs if r.get("phase") in ("short", "long") and r.get("token_sha")]
    if not timed:
        return ["- n/a: token_sha (no data)"]
    within = {}
    across = {}
    for r in timed:
        within.setdefault((r.get("stage"), r["config"], r.get("round"), r["phase"]), set()).add(r["token_sha"])
        across.setdefault((r.get("stage"), r["config"], r["phase"]), set()).add(r["token_sha"])
    lines = []
    bad = [f"{c} {s}/{p} round {rd}: {len(v)} different" for (s, c, rd, p), v in within.items() if len(v) > 1]
    lines.append(check(not bad, "token_sha identical across reps within each session"
                       + (": " + "; ".join(bad) if bad else f" ({len(within)} sessions)")))
    bad = [f"{c} {s}/{p}: {len(v)} different" for (s, c, p), v in across.items() if len(v) > 1]
    lines.append(check(not bad, "token_sha identical across rounds within each config (fresh process each)"
                       + (": " + "; ".join(bad) if bad else "")))
    return lines


def shared_prefix(a, b):
    count = 0
    for x, y in zip(a, b):
        if x != y:
            break
        count += 1
    return count


def first_token_matrix(runs):
    main = of(runs, stage="main", phase="short")
    reference = {}
    for r in main:
        if isinstance(r.get("first_tokens"), list) and r["first_tokens"]:
            reference.setdefault(r["config"], r["first_tokens"])   # first run in file order
    names = ordered(reference)
    if len(names) < 2:
        return ["- n/a: first_tokens agreement (needs two configs with data)"]
    rows, differing = [], []
    for a in names:
        row = [a]
        for b in names:
            prefix = shared_prefix(reference[a], reference[b])
            row.append(prefix)
            if a < b and prefix < min(len(reference[a]), len(reference[b])):
                differing.append(f"{a} vs {b}: {prefix} of {min(len(reference[a]), len(reference[b]))}")
        rows.append(row)
    lines = [check(not differing, "first_tokens agree between all configs"
                   + (": diverge at " + "; ".join(differing) + " (expected between different "
                      "quantizations or devices; same IR on different devices should agree longer)"
                      if differing else "")), ""]
    lines += ["Shared prefix length of `first_tokens` (first short run per config, stage=main; "
              "diagonal = tokens recorded):", ""]
    return lines + table([""] + names, rows)


def drift_checks(runs):
    lines = []
    short = of(runs, stage="main", phase="short")
    for config in config_names(short):
        per_round = {}
        for r in of(short, config=config):
            rate = num(r.get("decode_tok_s"))
            if rate is not None and num(r.get("round")) is not None:
                per_round.setdefault(int(r["round"]), []).append(rate)
        medians = {k: statistics.median(v) for k, v in sorted(per_round.items())}
        shown = ", ".join(f"round {k}: {v:.2f}" for k, v in medians.items())
        if len(medians) < 2:
            lines.append(f"- n/a: drift {config}: fewer than two rounds with data ({shown or 'none'})")
            continue
        spread = max(medians.values()) / min(medians.values()) - 1
        lines.append(check(spread <= DRIFT_LIMIT,
                           f"drift {config}: {shown}; max/min spread {spread:.1%} "
                           f"(limit {DRIFT_LIMIT:.0%})"))
    return lines or ["- n/a: drift (no stage=main short runs)"]


def failed_checks(runs):
    failed = of(runs, phase="failed")
    if not failed:
        return [check(True, "no failed sessions")]
    return [check(False, f"failed session {r.get('config')} round {r.get('round', NA)} "
                         f"({r.get('stage', NA)}): code {r.get('code', NA)}") for r in failed]


def consistency(runs):
    lines = ["## 4. Consistency checks", ""]
    lines += values_agree(runs, "generated")
    lines += values_agree(runs, "prompt_tokens")
    lines += finish_reasons(runs)
    lines += sha_checks(runs)
    lines += first_token_matrix(runs)
    lines += drift_checks(runs)
    lines += failed_checks(runs)
    return lines + [""]


# ---------------------------------------------------------------- 5. sweep
def split_sweep_name(name):
    """'llama-cpu-t19' -> ('llama-cpu', '19'); a name without a -t suffix is its own runtime."""
    match = re.match(r"^(.+)-t(\w+)$", name)
    return (match.group(1), match.group(2)) if match else (name, NA)


def sweep_sort_key(name):
    runtime, threads = split_sweep_name(name)
    return (runtime, int(threads) if threads.isdigit() else -1)    # 'auto' first


def sweep_table(runs):
    sweep = of(runs, stage="sweep")
    if not sweep:
        return []
    lines = ["## 5. Thread sweep (stage=sweep)", ""]
    rows, best = [], {}
    for config in sorted(config_names(sweep), key=sweep_sort_key):
        mine = of(sweep, config=config)
        short = of(mine, phase="short")
        rates = nums(r.get("decode_tok_s") for r in short)
        runtime, threads = split_sweep_name(config)
        rows.append([config, runtime, threads, med_range(rates, 2),
                     med_range([r.get("ttft_s") for r in short], 3),
                     med_range([r.get("load_s") for r in of(mine, phase="load")], 2)])
        if rates and (runtime not in best or statistics.median(rates) > best[runtime][1]):
            best[runtime] = (config, statistics.median(rates), threads)
    lines += table(["config", "runtime", "threads", "decode tok/s", "short TTFT s", "load s"], rows)
    if best:
        for runtime, (config, rate, threads) in best.items():
            lines.append(f"- Best thread count for {runtime}: {threads} ({rate:.2f} tok/s, {config})")
    else:
        lines.append("- Best thread count: n/a (no decode rates)")
    return lines + [""]


# ---------------------------------------------------------------- 6. prove
def prove_table(prove):
    if prove is None:
        return []
    lines = ["## 6. Proof attempts (prove.jsonl)", ""]
    prove = [r for r in prove if isinstance(r.get("config"), str) and r.get("lemma") is not None]
    if not prove:
        return lines + ["No usable records.", ""]
    configs = config_names(prove)
    lemmas = list(dict.fromkeys(str(r["lemma"]) for r in prove))
    rows = []
    for lemma in lemmas:
        row = [lemma]
        for config in configs:
            cells = []
            for r in prove:
                if r["config"] == config and str(r["lemma"]) == lemma:
                    cells.append(f"{r.get('verdict') or NA} ({fmt(r.get('generated'), 0)} tok, "
                                 f"{fmt(r.get('decode_tok_s'), 1)} tok/s, {fmt(r.get('total_s'), 0)} s)")
            row.append("; ".join(cells) if cells else NA)
        rows.append(row)
    lines += table(["lemma"] + configs, rows)
    lines += ["Cells: verdict (generated tokens, decode tok/s, total seconds); several attempts "
              "for one cell are separated by semicolons.", ""]
    return lines


# ---------------------------------------------------------------- main
def build_report(runs, telemetry, prove, notes):
    lines = ["# Goedel-Prover-V2-8B benchmark summary", ""]
    lines += [f"Note: {note}" for note in notes]
    if notes:
        lines.append("")
    if runs is None:
        return lines + ["No runs file; nothing to summarize.", ""]
    lines += main_table(runs)
    lines += energy_table(runs, telemetry)
    lines += device_table(runs, telemetry)
    lines += consistency(runs)
    lines += sweep_table(runs)
    lines += prove_table(prove)
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", default=str(OUT / "runs.jsonl"))
    parser.add_argument("--telemetry", default=str(OUT / "telemetry.jsonl"))
    parser.add_argument("--prove", default=str(OUT / "prove.jsonl"))
    parser.add_argument("--report", default=str(OUT / "report.md"))
    args = parser.parse_args()

    notes = []
    runs, skipped = read_jsonl(args.runs)
    if skipped:
        notes.append(f"{skipped} unreadable line(s) skipped in {args.runs}.")
    if runs is not None:
        runs = [r for r in runs if isinstance(r.get("config"), str)]
    samples, skipped = read_jsonl(args.telemetry)
    if skipped:
        notes.append(f"{skipped} unreadable line(s) skipped in {args.telemetry}.")
    if samples is None:
        notes.append(f"telemetry file not found: {args.telemetry}.")
    telemetry = None if samples is None else Telemetry(samples)
    prove, skipped = read_jsonl(args.prove)
    if skipped:
        notes.append(f"{skipped} unreadable line(s) skipped in {args.prove}.")

    report = "\n".join(build_report(runs, telemetry, prove, notes)).rstrip() + "\n"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(report)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
