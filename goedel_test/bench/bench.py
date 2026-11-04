"""Compare Goedel-Prover-V2-8B on CPU, iGPU and NPU under identical conditions.

Tracks
  A  OpenVINO GenAI, one INT4 IR, on CPU / GPU / NPU: isolates the hardware.
  B  llama.cpp, the Q4_K_M GGUF, on CPU / Vulkan iGPU: the existing setup.

Subcommands
  telemetry            log energy, utilization and frequency once a second
  worker               run one session (one process, one model load)
  sweep                find the best CPU thread count for each runtime
  main                 round-robin speed benchmark
  prove                real lemmas, greedy, checked by Lean

Timing is taken on the client from per-token callbacks, with one definition for both
runtimes: TTFT = request start to first token; decode rate = (N-1) / (last - first token).
"""
import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
OUT = Path(os.environ.get("BENCH_OUT", HERE / "out"))  # the lab keeps its results apart
RUNS = OUT / "runs.jsonl"
TELEMETRY = OUT / "telemetry.jsonl"
PROVE_RESULTS = OUT / "prove.jsonl"
IR_DIR = ROOT / "models" / "Goedel-Prover-V2-8B-ov-int4-cw"
OV_CACHE = OUT / "ov_cache"
GGUF = ROOT / "models" / "Goedel-Prover-V2-8B.Q4_K_M.gguf"
LLAMA = {"cpu": ROOT / "llama.cpp-b12078", "vulkan": ROOT / "llama.cpp-b12078-vulkan"}
PORT = 8091

CONTEXT = 4096          # every configuration gets the same total context
MAX_PROMPT = 1024       # the NPU's KV cache is static: 1024 + 3072 = 4096
MIN_RESPONSE = CONTEXT - MAX_PROMPT
SHORT_TOKENS = 256      # generated per short-prompt repetition
LONG_TOKENS = 16        # long-prompt repetitions measure prompt processing
LONG_PROMPT_CHARS = 2100  # about 960 tokens: under the NPU's MAX_PROMPT
COOLDOWN_S = 45         # the last IDLE_S of this is the session's idle baseline
IDLE_S = 20


# ---------------------------------------------------------------- prompts
def wrap(user):
    """The chat template's output, written out so both runtimes get the same bytes."""
    return f"<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"


def lemma_prompt(name):
    from lemmas import LEMMAS
    from prove import HEADER, PROMPT
    statement = next(s for n, _, s in LEMMAS if n == name)
    return wrap(PROMPT.format(code=HEADER + statement + " := by\n  sorry\n"))


def short_prompt():
    return lemma_prompt("task_s")


def long_prompt(chars=LONG_PROMPT_CHARS):
    """All lemma statements, cut to a fixed length, so prompt processing dominates."""
    from lemmas import LEMMAS
    text = "\n\n".join(s for _, _, s in LEMMAS)
    while len(text) < chars:
        text += "\n\n" + text
    return wrap("Restate these Lean 4 theorems:\n\n" + text[:chars])


# ---------------------------------------------------------------- telemetry
class Item(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("status", wintypes.DWORD), ("value", ctypes.c_double)]


class Pdh:
    """Performance counters by English name, which works on a localized Windows."""
    FMT = 0x200 | 0x8000  # double, no cap at 100

    def __init__(self, paths):
        self.dll = ctypes.WinDLL("pdh")
        self.query = ctypes.c_void_p()
        if self.dll.PdhOpenQueryW(None, None, ctypes.byref(self.query)):
            raise OSError("PdhOpenQuery failed")
        self.counters = {}
        for key, path in paths.items():
            handle = ctypes.c_void_p()
            status = self.dll.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(handle))
            if status == 0:
                self.counters[key] = handle
            else:
                print(f"counter unavailable: {path} ({status:#x})", file=sys.stderr)
        self.dll.PdhCollectQueryData(self.query)

    def read(self):
        self.dll.PdhCollectQueryData(self.query)
        result = {}
        for key, handle in self.counters.items():
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            self.dll.PdhGetFormattedCounterArrayW(
                handle, self.FMT, ctypes.byref(size), ctypes.byref(count), None)
            if not size.value:
                continue
            buffer = ctypes.create_string_buffer(size.value)
            status = self.dll.PdhGetFormattedCounterArrayW(
                handle, self.FMT, ctypes.byref(size), ctypes.byref(count), buffer)
            if status:
                continue
            items = ctypes.cast(buffer, ctypes.POINTER(Item))
            result[key] = {items[i].name: items[i].value for i in range(count.value)}
        return result


def telemetry(path, interval=1.0):
    pdh = Pdh({
        "energy": r"\Energy Meter(*)\Energy",
        "power": r"\Energy Meter(*)\Power",
        "engine": r"\GPU Engine(*)\Utilization Percentage",
        "adapter_mem": r"\GPU Adapter Memory(*)\Total Committed",
        "cpu_util": r"\Processor Information(_Total)\% Processor Utility",
        "cpu_time": r"\Processor Information(_Total)\% Processor Time",
        "cpu_mhz": r"\Processor Information(_Total)\Actual Frequency",
    })
    import psutil
    with open(path, "a", encoding="utf-8") as out:
        while True:
            time.sleep(interval)
            raw = pdh.read()
            engines = {}
            for name, value in raw.get("engine", {}).items():
                # pid_N_luid_A_B_phys_0_eng_0_engtype_X -> A_B:X, summed over processes
                if "_luid_" in name and "_engtype_" in name:
                    luid = name.split("_luid_")[1].split("_phys_")[0]
                    key = f"{luid}:{name.split('_engtype_')[1]}"
                    engines[key] = engines.get(key, 0) + value
            record = {
                "t": time.time(),
                # Cumulative, in picowatt-hours (checked against Power: 1 J = 2.78e8 units).
                "energy_pwh": {k.lower(): v for k, v in raw.get("energy", {}).items()},
                "power_mw": {k.lower(): v for k, v in raw.get("power", {}).items()},
                "engines": {k: round(v, 1) for k, v in engines.items() if v > 0.5},
                "adapter_mem": raw.get("adapter_mem", {}),
                "cpu_util": raw.get("cpu_util", {}).get("_Total"),
                "cpu_time": raw.get("cpu_time", {}).get("_Total"),
                "cpu_mhz": raw.get("cpu_mhz", {}).get("_Total"),
                "ram_used_gb": round(psutil.virtual_memory().used / 2**30, 2),
            }
            out.write(json.dumps(record) + "\n")
            out.flush()


# ---------------------------------------------------------------- runtimes
class Result:
    def __init__(self, stamps, tokens, text, prompt_tokens, finish, start, end):
        self.stamps, self.tokens, self.text = stamps, tokens, text
        self.prompt_tokens, self.finish = prompt_tokens, finish
        self.start, self.end = start, end


class OpenVinoSession:
    def __init__(self, device, threads, hint, cache_tag, ir=None, extra=None, context=None):
        import openvino_genai as genai
        self.genai = genai
        self.ir = ROOT / "models" / ir if ir else IR_DIR
        # Separate caches per stage and hint, so the main stage's first load is really cold.
        props = {"CACHE_DIR": str(OV_CACHE / "_".join(filter(None, [cache_tag, device, hint])))}
        if device == "CPU" and os.environ.get("BENCH_CPU_CACHE") == "0":
            del props["CACHE_DIR"]  # saves little on CPU, and each one takes about 4.5 GB of disk
        if device == "NPU":
            max_prompt, total = context or (MAX_PROMPT, CONTEXT)
            props.update(MAX_PROMPT_LEN=max_prompt, MIN_RESPONSE_LEN=total - max_prompt)
            if hint:
                props.update(GENERATE_HINT=hint)
        else:
            # By default CPU and GPU serve a repeated prompt from cache, which fakes TTFT
            # (0.14 s against a real 1.5 s). The NPU and llama.cpp both reprocess it.
            scheduler = genai.SchedulerConfig()
            scheduler.enable_prefix_caching = False
            props["scheduler_config"] = scheduler
        if device == "CPU" and threads:
            props["INFERENCE_NUM_THREADS"] = threads
        props.update(extra or {})
        self.pipe = genai.LLMPipeline(str(self.ir), device, **props)

    def generate(self, prompt, max_tokens, ignore_eos):
        genai = self.genai
        config = self.pipe.get_generation_config()
        config.max_new_tokens = max_tokens
        config.do_sample = False
        config.ignore_eos = ignore_eos
        config.repetition_penalty = 1.0
        config.apply_chat_template = False
        stamps, tokens = [], []

        class Clock(genai.StreamerBase):
            def write(self, token):
                now = time.perf_counter()
                new = token if isinstance(token, list) else [token]
                tokens.extend(int(t) for t in new)
                stamps.extend([now] * len(new))
                return genai.StreamingStatus.RUNNING

            def end(self):
                pass

        wall_start, start = time.time(), time.perf_counter()
        output = self.pipe.generate([prompt], config, streamer=Clock())
        wall_end = time.time()
        metrics = output.perf_metrics
        finish = "length" if len(tokens) >= max_tokens else "stop"
        return Result([s - start for s in stamps], tokens, output.texts[0],
                      metrics.get_num_input_tokens(), finish, wall_start, wall_end)

    def peak_gb(self):
        import psutil
        return psutil.Process().memory_info().peak_wset / 2**30

    def close(self):
        del self.pipe


class LlamaSession:
    def __init__(self, backend, threads):
        folder = LLAMA[backend]
        layers = "99" if backend == "vulkan" else "0"
        self.log = open(OUT / f"llama_{backend}.log", "w", encoding="utf-8")
        self.server = subprocess.Popen(
            [str(folder / "llama-server.exe"), "-m", str(GGUF), "-t", str(threads), "-tb", str(threads),
             "-c", str(CONTEXT), "-np", "1", "-ngl", layers, "--host", "127.0.0.1", "--port", str(PORT),
             "--no-webui"],
            stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 900
        while True:
            if self.server.poll() is not None:
                raise RuntimeError(f"llama-server exited with {self.server.returncode}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.25)

    def generate(self, prompt, max_tokens, ignore_eos):
        body = json.dumps({
            "prompt": prompt, "n_predict": max_tokens, "temperature": 0, "repeat_penalty": 1.0,
            "cache_prompt": False, "ignore_eos": ignore_eos, "stream": True, "return_tokens": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/completion", body, {"Content-Type": "application/json"})
        stamps, tokens, text, final = [], [], [], {}
        wall_start, start = time.time(), time.perf_counter()
        with urllib.request.urlopen(request, timeout=14400) as response:
            for line in response:
                if not line.startswith(b"data: "):
                    continue
                now = time.perf_counter()
                chunk = json.loads(line[6:])
                new = chunk.get("tokens") or []  # the final chunk may carry the last token
                tokens.extend(new)
                stamps.extend([now - start] * len(new))
                text.append(chunk.get("content", ""))
                if chunk.get("stop"):
                    final = chunk
                    break
        wall_end = time.time()
        finish = "length" if final.get("stop_type") == "limit" else "stop"
        return Result(stamps, tokens, "".join(text), final.get("tokens_evaluated"), finish, wall_start, wall_end)

    def peak_gb(self):
        import psutil
        return psutil.Process(self.server.pid).memory_info().peak_wset / 2**30

    def close(self):
        self.server.terminate()
        self.server.wait(timeout=60)
        self.log.close()


def open_session(config):
    if config["runtime"] == "hybrid":
        from hybrid import HybridSession
        return HybridSession(config["ir"], config["device"], config.get("threads"), config["chunk"],
                             config.get("max_prompt", MAX_PROMPT), config.get("cache_tag"))
    if config["runtime"] == "openvino":
        return OpenVinoSession(config["device"], config.get("threads"), config.get("hint"),
                               config.get("cache_tag"), config.get("ir"), config.get("extra"),
                               config.get("context"))
    return LlamaSession(config["device"], config.get("threads", 4))


# ---------------------------------------------------------------- worker
def stats(result):
    n = len(result.tokens)
    decode = (n - 1) / (result.stamps[-1] - result.stamps[0]) if n > 1 and result.stamps[-1] > result.stamps[0] else None
    return {
        "prompt_tokens": result.prompt_tokens, "generated": n,
        "ttft_s": round(result.stamps[0], 4) if n else None,
        "decode_tok_s": round(decode, 3) if decode else None,
        "total_s": round(result.end - result.start, 3),
        "start": result.start, "end": result.end, "finish": result.finish,
        "token_sha": hashlib.sha256(json.dumps(result.tokens).encode()).hexdigest()[:16],
        "first_tokens": result.tokens[:64],
    }


def emit(record):
    with RUNS.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {k: record.get(k) for k in ("config", "round", "phase", "rep", "prompt_tokens", "generated",
                                          "ttft_s", "decode_tok_s", "total_s", "load_s")}
    print(json.dumps(summary), flush=True)


def worker(config, plan):
    base = {"config": config["name"], "round": plan["round"], "stage": plan["stage"]}
    start = time.perf_counter()
    session = open_session(config)
    load = time.perf_counter() - start
    emit({**base, "phase": "load", "load_s": round(load, 2)})
    try:
        session.generate(short_prompt(), 16, True)  # warmup, discarded
        for rep in range(plan["short_reps"]):
            result = session.generate(short_prompt(), plan["short_tokens"], True)
            emit({**base, "phase": "short", "rep": rep, **stats(result), "text": result.text[:600]})
        for rep in range(plan.get("deep_reps", 0)):  # decode slows as the KV cache grows
            result = session.generate(short_prompt(), plan["deep_tokens"], True)
            emit({**base, "phase": "deep", "rep": rep, **stats(result)})
        for rep in range(plan["long_reps"]):
            result = session.generate(long_prompt(), LONG_TOKENS, True)
            emit({**base, "phase": "long", "rep": rep, **stats(result)})
        for rep in range(plan.get("xlong_reps", 0)):  # a proof-repair sized prompt
            result = session.generate(long_prompt(plan["xlong_chars"]), LONG_TOKENS, True)
            emit({**base, "phase": "xlong", "rep": rep, **stats(result)})
        emit({**base, "phase": "memory", "worker_peak_gb": round(session.peak_gb(), 2)})
    finally:
        session.close()


# ---------------------------------------------------------------- orchestration
class MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def commit_free_gb():
    """Commit still available. Unlike "available RAM", this ignores memory-mapped model files,
    which Windows can always reclaim, so loading a model doesn't look like exhaustion."""
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullAvailPageFile / 2**30


def wait_for_memory(min_free_gb=20, limit_s=120):
    """A killed process frees its memory gradually: don't start the next one too early."""
    deadline = time.time() + limit_s
    while commit_free_gb() < min_free_gb and time.time() < deadline:
        time.sleep(2)


def watch(process, timeout, min_free_gb=2.0):
    """Wait for a session, killing it on timeout or before it can exhaust the machine's memory."""
    deadline = time.time() + timeout
    while process.poll() is None:
        reason = None
        if time.time() > deadline:
            reason = "timeout"
        elif commit_free_gb() < min_free_gb:
            reason = "ram_guard"
        if reason:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
            process.wait()
            return reason
        time.sleep(1)
    return process.returncode


def session_process(config, plan, timeout, min_free_gb=2.0):
    """One fresh process per session, preceded by an idle baseline."""
    time.sleep(COOLDOWN_S - IDLE_S)
    idle_start = time.time()
    time.sleep(IDLE_S)
    emit({"config": config["name"], "round": plan["round"], "stage": plan["stage"], "phase": "idle",
          "start": idle_start, "end": time.time()})
    args = [sys.executable, __file__, "worker", "--config", json.dumps(config), "--plan", json.dumps(plan)]
    code = watch(subprocess.Popen(args), timeout, min_free_gb)
    if code != 0:
        emit({"config": config["name"], "round": plan["round"], "stage": plan["stage"],
              "phase": "failed", "code": code})
        print(f"SESSION_FAILED {config['name']} round {plan['round']}: {code}", flush=True)
    return code == 0


def with_telemetry(function):
    OUT.mkdir(exist_ok=True)
    sampler = subprocess.Popen([sys.executable, __file__, "telemetry"])
    try:
        function()
    finally:
        sampler.terminate()


def run_sweep():
    plan = {"stage": "sweep", "round": 0, "short_reps": 2, "short_tokens": 96, "long_reps": 0}
    for threads in (8, 14, 19, 20):
        session_process({"name": f"llama-cpu-t{threads}", "runtime": "llama", "device": "cpu",
                         "threads": threads}, plan, 3600)
    for threads in (None, 8, 14, 20):
        session_process({"name": f"ov-cpu-t{threads or 'auto'}", "runtime": "openvino", "device": "CPU",
                         "threads": threads, "cache_tag": "sweep"}, plan, 3600)
    print("SWEEP_DONE", flush=True)


def run_matrix(args):
    """Run a JSON test plan: {"stage", "rounds", "plan": {...}, "timeout", "configs": [...]}.

    Configurations rotate between rounds. A failure is logged and the plan carries on.
    """
    spec = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    order = spec["configs"]
    for round_index in range(spec.get("rounds", 1)):
        shift = round_index % len(order)
        for config in order[shift:] + order[:shift]:
            if shutil.disk_usage(ROOT).free < 6 * 2**30:
                print("MATRIX_STOPPED low disk", flush=True)
                return
            plan = {"stage": spec["stage"], "round": round_index, **spec["plan"], **config.get("plan", {})}
            session_process({**config, "cache_tag": spec["stage"]}, plan, spec.get("timeout", 3600),
                            spec.get("min_free_gb", 2.0))
    print(f"MATRIX_DONE {spec['stage']}", flush=True)


def run_smoke(args):
    """A short pass over every configuration, with the riskiest (NPU) last."""
    plan = {"stage": "smoke", "round": 0, "short_reps": 1, "short_tokens": 32, "long_reps": 1}
    order = sorted(configs(args), key=lambda c: (c["device"] == "NPU", c["name"]))
    if args.only:
        order = [c for c in order if c["name"] in args.only.split(",")]
    for config in order:
        session_process({**config, "cache_tag": "smoke"}, plan, 5400)
    print("SMOKE_DONE", flush=True)


def configs(args):
    return [
        {"name": "A-OV-CPU", "runtime": "openvino", "device": "CPU", "threads": args.ov_threads},
        {"name": "A-OV-iGPU", "runtime": "openvino", "device": "GPU"},
        {"name": "A-OV-NPU", "runtime": "openvino", "device": "NPU", "hint": args.npu_hint},
        {"name": "B-llama-CPU", "runtime": "llama", "device": "cpu", "threads": args.llama_threads},
        {"name": "B-llama-iGPU", "runtime": "llama", "device": "vulkan", "threads": 4},
    ]


def run_main(args):
    order = configs(args)
    if args.only:
        order = [c for c in order if c["name"] in args.only.split(",")]
    for round_index in range(args.rounds):
        shift = round_index % len(order)
        for config in order[shift:] + order[:shift]:
            plan = {"stage": "main", "round": round_index, "short_reps": args.reps,
                    "short_tokens": SHORT_TOKENS, "long_reps": 2}
            session_process(config, plan, 7200)
    print("MAIN_DONE", flush=True)


def run_prove(args):
    import prove
    from lemmas import LEMMAS
    prove.WORK = prove.LEAN_PROJECT / "attempts_bench"
    statements = {n: s for n, _, s in LEMMAS}
    names = args.lemmas.split(",")
    setups = json.loads(Path(args.matrix).read_text(encoding="utf-8"))["configs"] if args.matrix else configs(args)
    for config in setups:
        if args.only and config["name"] not in args.only.split(","):
            continue
        session = open_session(config)
        try:
            for name in names:
                result = session.generate(lemma_prompt(name), args.max_tokens, False)
                lean_code = prove.extract_code(result.text)
                if result.finish == "length" and lean_code is None:
                    verdict, detail = "truncated", ""
                else:
                    verdict, detail = prove.judge(name, statements[name], lean_code)
                record = {"config": config["name"], "lemma": name, "verdict": verdict, **stats(result),
                          "detail": detail, "reply": result.text}
                with PROVE_RESULTS.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{config['name']} {name}: {verdict} | {record['generated']} tokens, "
                      f"{record['decode_tok_s']} tok/s, {record['total_s']:.0f} s", flush=True)
        finally:
            session.close()
    print("PROVE_DONE", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["telemetry", "worker", "smoke", "sweep", "main", "prove", "matrix"])
    parser.add_argument("--matrix")
    parser.add_argument("--config")
    parser.add_argument("--plan")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--ov-threads", type=int)
    parser.add_argument("--llama-threads", type=int, default=19)
    parser.add_argument("--npu-hint")
    parser.add_argument("--only", default="")
    parser.add_argument("--lemmas", default="task_a,task_b,task_c")
    parser.add_argument("--max-tokens", type=int, default=3000)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.command == "telemetry":
        telemetry(TELEMETRY)
    elif args.command == "worker":
        worker(json.loads(args.config), json.loads(args.plan))
    elif args.command == "matrix":
        with_telemetry(lambda: run_matrix(args))
    elif args.command == "smoke":
        with_telemetry(lambda: run_smoke(args))
    elif args.command == "sweep":
        with_telemetry(run_sweep)
    elif args.command == "main":
        with_telemetry(lambda: run_main(args))
    elif args.command == "prove":
        with_telemetry(lambda: run_prove(args))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
