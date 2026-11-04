"""Send each lemma to a local llama-server, compile every attempt with Lean, log to JSONL.

Runs unattended. A proof passes only if Lean compiles it, the statement is unchanged,
there is no sorry, and it uses only Lean's standard axioms.

  python prove.py --check             compile the statements with sorry, no model
  python prove.py --attempts 4        run the model
  python prove.py --attempts 4 --only task_s,task_f
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from lemmas import LEMMAS

HERE = Path(__file__).resolve().parent
LEAN_PROJECT = HERE.parent / "lean49"
WORK = LEAN_PROJECT / "attempts"
RESULTS = HERE / "results.jsonl"
SERVER = "http://127.0.0.1:8080/v1/chat/completions"
LAKE = str(Path.home() / ".elan" / "bin" / "lake.exe")
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
# Things that could make a proof pass without proving the statement.
FORBIDDEN = re.compile(r"\b(axiom|sorry|admit|native_decide|unsafe|implemented_by|macro|syntax|notation|elab)\b")

HEADER = """import Mathlib
import Aesop

set_option maxHeartbeats 400000

open BigOperators Real Nat Topology Rat

"""

PROMPT = """Complete the following Lean 4 code:

```lean4
{code}```

Before producing the Lean 4 code to formally prove the given theorem, provide a detailed proof plan outlining the main proof steps and strategies.
The plan should highlight key ideas, intermediate lemmas, and proof structures that will guide the construction of the final formal proof."""


def normalize(text):
    return " ".join(text.split())


def compile_lean(code, name):
    WORK.mkdir(exist_ok=True)
    path = WORK / f"{name}.lean"
    path.write_text(code, encoding="utf-8")
    start = time.time()
    try:
        run = subprocess.run(
            [LAKE, "env", "lean", str(path)], cwd=LEAN_PROJECT, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "lean timeout (300 s)", time.time() - start
    return run.returncode == 0, run.stdout + run.stderr, time.time() - start


def ask_model(code, max_tokens, temperature):
    body = json.dumps({
        "messages": [{"role": "user", "content": PROMPT.format(code=code)}],
        "max_tokens": max_tokens, "temperature": temperature,
    }).encode("utf-8")
    request = urllib.request.Request(SERVER, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=7200) as response:
        return json.load(response)


def extract_code(text):
    blocks = re.findall(r"```lean4?\s*\n(.*?)```", text, flags=re.S)
    return blocks[-1] if blocks else None


def judge(name, statement, code):
    """Return (verdict, compiler output)."""
    if code is None:
        return "no_code", ""
    if FORBIDDEN.search(code):
        return "forbidden_word", FORBIDDEN.search(code).group(0)
    if normalize(statement) not in normalize(code):
        return "statement_changed", ""
    if "import Mathlib" not in code:
        code = HEADER + code
    ok, output, _ = compile_lean(code + f"\n\n#print axioms {name}\n", name)
    if not ok or "error:" in output:
        return "compile_error", output[:4000]
    if "sorry" in output:
        return "uses_sorry", output[:4000]
    match = re.search(r"depends on axioms: \[(.*?)\]", output, flags=re.S)
    if match:
        axioms = {a.strip() for a in match.group(1).split(",")}
        if not axioms <= ALLOWED_AXIOMS:
            return "bad_axioms", output[:4000]
    elif "does not depend on any axioms" not in output:
        return "axioms_unknown", output[:4000]
    return "PROVED", output[:4000]


def check_statements():
    for name, _, statement in LEMMAS:
        ok, output, seconds = compile_lean(HEADER + statement + " := by\n  sorry\n", "check_" + name)
        errors = [line for line in output.splitlines() if "error" in line]
        print(f"{'ok ' if ok and not errors else 'BAD'} {name} ({seconds:.0f} s)")
        for line in errors:
            print("   ", line[:300])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    if args.check:
        check_statements()
        return
    only = set(filter(None, args.only.split(",")))
    for name, tier, statement in LEMMAS:
        if only and name not in only:
            continue
        code = HEADER + statement + " := by\n  sorry\n"
        for attempt in range(1, args.attempts + 1):
            start = time.time()
            reply = ask_model(code, args.max_tokens, args.temperature)
            seconds = time.time() - start
            choice = reply["choices"][0]
            text = choice["message"]["content"] or ""
            lean_code = extract_code(text)
            if choice.get("finish_reason") == "length" and lean_code is None:
                verdict, detail = "truncated", ""
            else:
                verdict, detail = judge(f"{name}", statement, lean_code)
            timings = reply.get("timings", {})
            record = {
                "lemma": name, "tier": tier, "attempt": attempt, "verdict": verdict,
                "model_seconds": round(seconds), "tokens": timings.get("predicted_n"),
                "tok_per_s": round(timings.get("predicted_per_second", 0), 2),
                "finish": choice.get("finish_reason"), "detail": detail, "reply": text,
            }
            with RESULTS.open("a", encoding="utf-8") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{name} #{attempt}: {verdict} | {seconds:.0f} s, "
                  f"{record['tokens']} tokens, {record['tok_per_s']} tok/s", flush=True)
            if verdict == "PROVED":
                break


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
