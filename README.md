# Gödel-Prover-V2-8B on an Intel Core Ultra 7 265K: CPU, iGPU, NPU and hybrids

Can a desktop's integrated GPU or NPU run a Lean theorem-proving model usefully, and how much energy does it
save over the CPU? This repo holds the benchmark's performance data, scripts and full results.

*AI agents: [REPORT_FOR_AI.md](REPORT_FOR_AI.md) has the same findings in a structured form, and
[results/summary.json](results/summary.json) has the numbers.*

## The short answer

**Use the iGPU with the `int4-asym-gq128` recipe.** It finished the most proofs, with the lowest time and energy
per finished proof.

| Setup | Proof tasks finished | Minutes | kJ per proof |
|---|---|---|---|
| **iGPU, `int4-asym-gq128`** | **5/6** | **33.0** | **14.3** |
| iGPU, `int4-cw-awq-se` | 4/6 | 39.0 | 22.2 (about 19 †) |
| iGPU, `int4-cw` | 3/6 | 41.1 | 30.4 |
| CPU, `int4-cw` | 3/6 | 34.0 | 66.9 |
| NPU, `int4-cw`, new driver | 2/6 | 54.0 | 41.6 (about 32 †) |
| NPU, `int4-cw-awq-se`, new driver | 1/6 | 54.9 | 83.9 (about 64 †) |
| NPU, `int4-cw`, old driver | 1/6 | 57.6 | 77.5 |
| Hybrid NPU prefill + iGPU decode, `int4-cw` (3 tasks only) | 1/3 | 24.3 | 54.8 |

† Estimate. A background process inflated these runs, as explained [below](#the-search-indexer-problem).

- **Six tasks is a small sample.** One attempt per task can't settle small differences, and a one-proof gap
  here is within the noise. The 5/6 result does agree with a separate accuracy test.
- **The NPU is the most energy-efficient per token** (3.9 J, against 5.3 J for the iGPU and 13.2 J for the CPU, in
  the same test), but it finished the fewest proofs, and failed attempts still use energy.
- **The CPU decodes only about 5% faster than the iGPU, at about 2.5 times the energy per token.**
- **Hybrids run, but didn't beat a single device** on anything that matters for proofs.
- **A newer NPU driver and the AWQ recipe didn't change the conclusion.**
- **This PC's single memory channel may be limiting all of these results.** See
  [Memory may be the real limit](#memory-may-be-the-real-limit).

## Test machine

| Part | Details |
|---|---|
| CPU | Intel Core Ultra 7 265K, 20 cores |
| iGPU | Intel Graphics, driver 32.0.101.5869 (dated August 2024) |
| NPU | Intel AI Boost (NPU 3720), driver 32.0.100.4239, later 32.0.100.5540 |
| RAM | One 32 GB DDR5 module at 4800 MT/s, in a 4-slot board, so single-channel |
| OS | Windows 11 Pro, build 26300 |
| Runtimes | OpenVINO and GenAI 2026.4.1, llama.cpp b12078 |
| Conversion | optimum 2.3.0, optimum-intel 2.2.0, NNCF 3.4.0, transformers 5.5.4, torch 2.15.0, Python 3.13.5 |
| Proof checking | Lean 4.9.0, Mathlib v4.9.0 |

## Model and recipes

The model is [Goedel-LM/Goedel-Prover-V2-8B](https://huggingface.co/Goedel-LM/Goedel-Prover-V2-8B), compressed from
15.3 GB to about 4.4–4.5 GB by storing weights in 4 bits. Recipes differ in how they do that.

| Name part | Meaning |
|---|---|
| `int4` | 4-bit integer weights |
| `cw` | Channel-wise: one scale factor per channel |
| `gq128` | Group-wise: one scale factor per 128 weights. More accurate and slightly larger |
| `asym` | Each scale also gets an offset. Names without it are symmetric |
| `i8sym` | The few weights kept at 8 bits use symmetric format |
| `nf4` | 4-bit values with uneven spacing |
| `awq` | Activation-aware quantization: uses sample text to protect the weights that matter most |
| `se` | Scale estimation: fine-tunes the scales against the same sample text |

OpenVINO's [NPU guide](https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai/inference-with-genai-on-npu.html)
requires symmetric 4-bit weights for LLMs on the NPU, and recommends group size 128 for models up to about 4–5B
parameters. That's why `int4-asym-gq128` wasn't tried on the NPU.

## How it was measured

- **Isolation:** each session is a fresh process with a fresh model load, after a 45-second cooldown. The last
  20 seconds of each cooldown are logged as that session's idle power. Setups rotate between rounds.
- **Speed:** timed on the client from per-token callbacks. First-token time runs from the request to the first token.
  Decode rate is (N-1) / (last token time - first token time). A warm-up generation is discarded.
- **Fairness:** greedy decoding, and the same prompts and total context across setups. OpenVINO's prefix cache is off
  on CPU and iGPU, because it serves repeated prompts from cache (0.14 s against a real 1.5 s).
- **Energy:** the CPU package counter (RAPL), through Windows' Energy Meter counter, read once a second. Figures
  include idle power. The DRAM domain reads 0, and wall power wasn't measured.
- **Proof tasks:** six Lean 4 tasks, with one greedy attempt per task, up to 3,600 tokens. Lean decides: the statement
  must be unchanged, the code must compile, it can't contain words like `sorry` or `axiom`, and it may use only Lean's
  standard axioms. The task statements and the model's outputs aren't published, so tasks appear as `task_a` to `task_f`.
- **Accuracy:** each recipe's next-token predictions are compared with the uncompressed PyTorch model at 2,573
  positions of Lean proofs. Greedy decoding follows these predictions directly.

## Results

### Proof tasks by setup

| Task | iGPU asym | iGPU AWQ | iGPU `cw` | CPU `cw` | NPU `cw` new driver | NPU AWQ new driver | NPU `cw` old driver | Hybrid `cw` |
|---|---|---|---|---|---|---|---|---|
| `task_a` | proved | proved | proved | proved | proved | proved | proved | truncated |
| `task_b` | proved | proved | proved | proved | truncated | unfinished | truncated | proved |
| `task_c` | proved | unfinished | unfinished | unfinished | unfinished | unfinished | unfinished | unfinished |
| `task_d` | proved | proved | unfinished | truncated | truncated | truncated | unfinished | not run |
| `task_e` | proved | proved | proved | proved | proved | Lean error | unfinished | not run |
| `task_f` | truncated | truncated | truncated | truncated | truncated | truncated | truncated | not run |

**Unfinished** means the run hit the token limit and its last code block still contained `sorry`. **Truncated** means
it hit the limit before finishing any code block. Prefill was under 1% of the run time for every setup (0.18–0.61%).

### Accuracy against the original weights

| Recipe | Same top token | Same top token where the original is confident | Top-5 overlap |
|---|---|---|---|
| `int4-asym-gq128`, CPU | 98.02% | 100.00% | 90.98% |
| `int4-gq128`, CPU | 97.86% | 99.82% | 90.16% |
| `nf4-cw`, CPU | 97.36% | 99.91% | 88.82% |
| `int4-cw-awq-se`, CPU | 97.01% | 99.83% | 85.81% |
| `int4-cw`, iGPU | 96.66% | 99.70% | 83.63% |
| `int4-cw`, CPU | 96.54% | 99.65% | 83.57% |
| `int4-cw-i8sym`, CPU | 96.54% | 99.65% | 83.54% |
| Plain Qwen3, CPU (control: different weights) | 83.48% | 88.60% | 59.55% |

"Confident" means the original's top two tokens differ by more than 1.0 in log-probability.

### Speed and energy (old NPU driver, 2 rounds)

| Setup | Decode tok/s | First token, 229 / 974 tokens (s) | Package W | J/token, 1,024 tokens | Peak RAM GB |
|---|---|---|---|---|---|
| NPU, chunk 1024 (default) | 5.73 | 4.71 / 4.76 | 22.8 | 4.00 | 10.5 |
| NPU, chunk 128 | 5.69 | 1.05 / 4.48 | 22.3 | 3.92 | 10.3 |
| NPU, chunk 256 | 5.73 | 1.27 / 5.22 | 22.4 | 3.93 | 10.4 |
| NPU, chunk 128, context 2048 | 6.13 | 1.05 / 4.57 | 22.6 | 3.67 | 10.3 |
| NPU, chunk 128, `BEST_PERF` | 4.96 | 1.05 / 4.57 | 20.5 | 4.18 | 10.4 |
| iGPU, `int4-cw` | 7.27 | 2.70 / 7.08 | 37.4 | 5.30 | 7.5 |
| iGPU, `int4-asym-gq128` | 7.06 | 2.99 / 7.59 | 36.7 | 5.24 | 8.0 |
| CPU, `int4-cw` | 7.60 | 1.28 / 5.00 | 99.3 | 13.21 | 8.0 |
| Hybrid NPU 128 → iGPU | 6.98 | 3.09 / 12.23 | 38.1 | 5.64 | 13.3 |
| Hybrid NPU 256 → iGPU | 6.79 | 2.51 / 9.81 | 38.2 | 5.94 | 13.5 |
| Hybrid NPU 128 → CPU | 7.68 | 3.18 / 12.16 | 93.9 | 13.28 | 16.0 |

Idle package power in this stage was 17.2 W (median). Power figures include idle.

Things worth noticing:

- **`NPUW_LLM_PREFILL_CHUNK_SIZE=128` is the best NPU setting found.** First token on a short prompt is about 4.5 times
  faster, with no decode cost.
- **A smaller NPU context decodes faster:** 6.13 tok/s at 2,048 tokens, 5.69 at 4,096 and 5.26 at 8,192 (the last in a
  different stage). But 2,048 is too small for the longer proofs.
- **`BEST_PERF` was slower here,** 4.96 against 5.69 tok/s, although the docs describe it as the best-performance mode.
- **The iGPU with an f16 KV cache crashed in both rounds,** so the cost of that setting couldn't be measured alone.

### Recipes by device (old NPU driver, 2 rounds)

| Setup | Decode tok/s | First token, 229 tokens (s) | Package W | J/token, 256 tokens |
|---|---|---|---|---|
| NPU, `int4-cw` | 5.73 | 4.64 | 22.7 | 4.27 |
| NPU, official Qwen3 `int4-cw` | 5.71 | 4.75 | 21.9 | 4.16 |
| NPU, `int4-cw-i8sym` | 5.76 | 4.74 | 22.8 | 4.25 |
| NPU, `int4-gq128` | 4.02 | 10.19 | 21.9 | 6.22 |
| NPU, `nf4-cw` | failed in both rounds | | | |
| iGPU, `int4-cw` | 6.86 | 2.84 | 37.1 | 5.64 |
| iGPU, `int4-asym-gq128` | 7.07 | 2.88 | 36.3 | 5.41 |
| iGPU, `nf4-cw` | failed in both rounds | | | |
| CPU, `int4-cw` | 7.96 | 1.18 | 96.5 | 12.11 |
| CPU, `int4-asym-gq128` | 7.35 | 1.18 | 97.5 | 13.40 |
| CPU, `nf4-cw` | 6.89 | 2.30 | 129.5 | 19.45 |

OpenVINO's [official Qwen3 NPU model](https://huggingface.co/OpenVINO/Qwen3-8B-int4-cw-ov) runs at the same speed as a
plain `int4-cw` conversion of Gödel, so its "NPU-optimized" label seems to come down to the recipe. It's a
different model, so it can't replace Gödel.

### Very long prompts (2,926 tokens, old NPU driver)

| Setup | First token (s) | Decode after it (tok/s) |
|---|---|---|
| NPU, chunk 128, context 8192 | 15.18 | 3.45 |
| NPU, chunk 1024, context 8192 | 16.60 | 3.47 |
| CPU | 15.89 | 7.52 |
| iGPU | 25.68 | 6.42 |
| Hybrid NPU 1024 → iGPU | 36.73 | 4.87 |
| Hybrid NPU 256 → iGPU | 37.21 | 4.81 |

### NPU driver update

| Setup | Decode tok/s, old driver | New driver, indexer busy | New driver, indexer stopped |
|---|---|---|---|
| Chunk 1024 | 5.73 | 5.43 | not run |
| Chunk 128 | 5.69 | 5.46 | 5.74 |
| Chunk 128, context 2048 | 6.13 | 5.90 | not run |
| Chunk 128, `BEST_PERF` | 4.96 | 4.80 | not run |
| `int4-gq128` | 4.02 | 4.05 | not run |

- **Speed didn't really change.** The 5.69 to 5.74 difference is within run-to-run spread, and first-token time at
  chunk 128 stayed about 1.05 s.
- **The driver changes the output.** Greedy tokens at chunk 128 match the old driver at 256 tokens but differ at
  1,024. Four of six proof outputs changed, which alone took NPU `int4-cw` from 1/6 to 2/6.
- **NF4 still fails,** now while compiling (`ZE_RESULT_ERROR_INVALID_NULL_POINTER`) instead of with `DEVICE_LOST`.
  The docs limit NF4 to Lunar Lake and later, and this machine has the older NPU 3720.

### AWQ (`int4-cw-awq-se`, new driver, indexer stopped)

| Setup | Decode tok/s | First token, 229 / 974 tokens (s) | Package W | J/token, 1,024 tokens | Peak RAM GB |
|---|---|---|---|---|---|
| NPU, AWQ, chunk 128 | 5.74 | 1.03 / 4.23 | 20.8 | 3.60 | 10.2 |
| NPU, `int4-cw`, chunk 128 | 5.74 | 1.03 / 4.23 | 20.7 | 3.57 | 10.2 |
| iGPU, AWQ | 7.13 | 2.95 / 7.42 | 35.3 | 5.06 | 7.5 |

- **AWQ costs nothing at run time.** Speed and energy match `int4-cw` on the NPU, and it follows the original weights
  more closely (97.01% against 96.54%). For an NPU that can't run NF4, it's the practical accuracy upgrade.
- **Its proof results weren't clearly better:** one proof more on the iGPU, one fewer on the NPU.
- **The conversion is expensive:** 34.1 minutes against about 1.5 minutes for the other recipes, including 9 minutes of
  AWQ and 22 minutes of scale estimation. It also used 2.1 GB of page file on this 32 GB machine.

### First benchmark: OpenVINO against llama.cpp

Three rounds, 3 proof tasks, 3,000-token limit, using `int4-cw` for OpenVINO and `Q4_K_M` for llama.cpp. Details are in
[goedel_test/bench/out/report.md](goedel_test/bench/out/report.md).

| Setup | Decode tok/s | First token, 229 tokens (s) | J/token, 256 tokens | Proved |
|---|---|---|---|---|
| OpenVINO, CPU | 7.83 | 1.22 | 13.13 | 2/3 |
| OpenVINO, iGPU | 6.70 | 2.79 | 6.31 | 2/3 |
| OpenVINO, NPU | 5.59 | 4.77 | 5.10 | 1/3 |
| llama.cpp, CPU | 7.14 | 1.45 | 15.36 | 3/3 |
| llama.cpp, iGPU (Vulkan) | 5.54 | 2.59 | 6.18 | 2/3 |

## The claims that prompted this

**["Where NPUs Fit into LLM Inference"](https://cascadia.to/blog/where-npus-fit-into-llm-inference) (Cascadia):** on an
Intel Core Ultra laptop with Llama 3.2 1B, NPU prefill plus CPU decode gave the first token 2.5 to 5.4 times faster than
CPU alone, growing with prompt length, at about the same decode speed and about 3 times the peak memory. The post calls
itself an example rather than a benchmark.

| Prompt tokens | NPU, chunk 128 (s) | CPU (s) | iGPU (s) | NPU gain over CPU | NPU gain over iGPU |
|---|---|---|---|---|---|
| 229 | 1.05 | 1.28 | 2.70 | 1.2× | 2.6× |
| 974 | 4.48 | 5.00 | 7.08 | 1.1× | 1.6× |
| 2,926 | 15.18 | 15.89 | 25.68 | 1.05× | 1.7× |

- **The large prefill gain didn't appear here.** Against this desktop CPU, the NPU's lead is small and doesn't grow.
  The model and hardware both differ from the post's, and the reason for the gap wasn't tested.
- **The memory cost did appear.** Hybrids peaked at 13–16 GB, against 7.5–8 GB for the iGPU or CPU alone and about
  10.5 GB for the NPU alone.
- **Prefill barely matters for proofs here.** It's under 1% of the run time, so even an ideal hybrid would gain little.

**[arXiv 2605.27435](https://arxiv.org/abs/2605.27435)** (Li, Qi and Chen) reports that on mobile chips CPUs can beat NPUs
in prefill, and NPUs give only 1.05–1.2× in decode, which it describes as memory-bound. On this desktop, the NPU decoded
slower than the CPU, and its prefill was only slightly faster.

**OpenVINO's hybrid routing:** the NPU plugin can send prefill and decode to different devices through its own options.
In one-shot probes, decode away from the NPU ran at only 0.2–1.7 tok/s or failed, and iGPU prefill with NPU decode kept
5.75 tok/s but took 10.7 s to the first token. So `hybrid.py` builds a custom handoff instead. Its NPU prefill takes
about 1.5 s per 128 tokens, against about 0.55 s for Intel's pipeline. The cause wasn't found, so these hybrids are
not at their best.

## The search indexer problem

Windows' `SearchIndexer` held about a full CPU core for hours, and read almost nothing whenever it was sampled. That
skewed the results:

| State | Idle package W | NPU chunk 128 decode tok/s | NPU package W |
|---|---|---|---|
| Indexer busy | 22.1 | 5.46 | 25.4 |
| `WSearch` and `DiagTrack` stopped | 13.6 | 5.74 | 20.7 |

The service restarted itself twice after being stopped, the second time at 16:20 during the last proof stage. So time
and energy for the later iGPU AWQ tasks and for both new-driver NPU proof runs are inflated:

- **iGPU AWQ:** about 7.47 tok/s at 35.8 W before it returned, about 6.5 tok/s at 39.4 W after.
- **NPU:** about 5.42 tok/s at 25.6 W during proofs, against 5.74 tok/s at 20.7 W with the indexer off.

The † estimates in the first table apply the clean rates and power to the same token counts. Which tasks were finished
shouldn't depend on this, because decoding is greedy and repeated runs of a setup gave identical tokens in the speed
tests. **If you benchmark on Windows, check this process first.**

The indexer doesn't explain all the variation, though. The iGPU also ran at two speeds with it off (6.58 and 7.69 tok/s
between rounds, each steady within its session), and that cause wasn't found.

## Memory may be the real limit

This is a hypothesis, not a measurement. Every device here shares one memory channel, and a decoded token has to read
close to the whole model. The model file is about 4.7 GB and one DDR5-4800 channel peaks at 38.4 GB/s, which
allows about **8.1 tokens per second**. The fastest single run on any device reached **8.02 tok/s**.

That would explain why such different devices land in the same narrow 5–8 tok/s band, and why the CPU's 100 W barely
beats the iGPU's 37 W. It also fits the arXiv paper's description of decode as memory-bound. Testing it would need a
second memory module, which wasn't available for these runs.

## Limits of this work

- **One machine, one attempt per task, six tasks.** Small differences in proofs don't mean much, and other Lean tasks
  could rank the setups differently.
- **The proof tasks aren't published,** so nobody else can check those verdicts or rerun the same tasks. The speed, energy
  and memory results don't depend on what the tasks say, though prompt lengths were fixed by them.
- **Runs from different stages aren't side by side.** Background load differed (idle was 17.2 W in the main stage and
  13.6 W in the AWQ stage), so treat energy comparisons across stages as rough.
- **Energy is the CPU package only.** There's no DRAM or wall-power reading, and no independent check that the package
  sensor captures all NPU power.
- **The iGPU driver dates from August 2024** and wasn't updated, so a newer one could change the iGPU numbers.
- **The accuracy positions come from llama.cpp's proofs,** cut to the first 1,024 tokens of each sequence.
- **Results are for greedy decoding** with no retries, repair or sampling, which real proof workflows often use.
- **`int4-asym-gq128` was not tried on the NPU,** because the docs require symmetric weights.

## Reproducing this

This is Windows-only: energy comes from Windows counters, and `prove.py` calls `lake.exe`. You need Python 3.13,
[elan](https://github.com/leanprover/elan) for Lean, and about 50 GB of disk. Scripts expect `models/` next to
`goedel_test/`. These steps were reconstructed from the run history and haven't been tested on a clean machine.

The tasks used here aren't included, so `goedel_test/lemmas.py` holds simple placeholders. Speed, energy and memory tests
run with them, but prompt lengths will differ, so replace them with tasks of similar length to compare closely. The
accuracy test also needs your own proofs, in the `reply` field of `out/prove.jsonl`, because this repo's data leaves
that field out.

```powershell
python -m venv ov_env
ov_env\Scripts\pip install openvino==2026.4.1 openvino-genai==2026.4.1.0 openvino-tokenizers==2026.4.1.0 nncf==3.4.0 optimum==2.3.0 optimum-intel==2.2.0 transformers==5.5.4 torch==2.15.0 datasets==5.0.1 psutil huggingface_hub

# Lean and Mathlib
cd lean49; lake exe cache get; cd ..

# Model and recipes (AWQ used 2.1 GB of page file on this 32 GB machine)
ov_env\Scripts\python goedel_test\bench\fetch_weights.py
ov_env\Scripts\python goedel_test\bench\fetch_reference.py
ov_env\Scripts\python goedel_test\bench\convert_all.py int4-cw int4-cw-i8sym int4-gq128 nf4-cw int4-asym-gq128 int4-cw-awq-se

# Tests: each lab_*.json is one stage. Check that SearchIndexer is idle first.
$env:PYTHONUTF8='1'; $env:BENCH_CPU_CACHE='0'; $env:BENCH_OUT="$PWD\goedel_test\bench\lab"
ov_env\Scripts\python goedel_test\bench\bench.py matrix --matrix goedel_test\bench\lab_stage3.json
ov_env\Scripts\python goedel_test\bench\accuracy.py all
ov_env\Scripts\python goedel_test\bench\accuracy.py recipe goedel-ov-int4-cw-awq-se CPU
ov_env\Scripts\python goedel_test\bench\lab_prove.py goedel_test\bench\lab_prove.json
ov_env\Scripts\python goedel_test\bench\lab_prove.py goedel_test\bench\lab_prove_awq.json

# Rebuild every table from the raw logs (standard library only)
python tools\make_summary.py
```

The first-benchmark llama.cpp runs also need [llama.cpp b12078](https://github.com/ggml-org/llama.cpp) and the
`Q4_K_M` GGUF in the folders named at the top of `bench.py`.

## What's in this repo

| Path | Contents |
|---|---|
| `results/summary.json` | Every table here, rebuilt from raw data by `tools/make_summary.py` |
| `goedel_test/bench/lab/` | Main study: `runs.jsonl`, `prove.jsonl`, `accuracy.jsonl`, `telemetry.jsonl` |
| `goedel_test/bench/out/` | First benchmark: the same data and its generated report |
| `goedel_test/bench/lab_*.json` | The exact test plans, with task names |
| `goedel_test/bench/*.py` | Benchmark, conversion, accuracy and hybrid code |
| `goedel_test/prove.py` | The Lean verdict logic |
| `goedel_test/lemmas.py` | Placeholder tasks |

**Not included:** model weights, task statements, model outputs, token IDs and run logs. The data files keep timing,
energy, memory and verdict fields only, plus a hash of each output so repeated runs can be compared. The code is as run,
apart from the task names and placeholders.

In `runs.jsonl`, stage `driver5540` is an interrupted run with possibly disturbed timings, so use `driver5540-rerun`
instead.

## How this was made

Claude Code (Anthropic's coding agent) planned and ran these tests, wrote the code and drafted these reports, directed
by the repo owner on one desktop PC. The tables in the reports use numbers rebuilt from the raw data by script. This
work hasn't been independently reviewed, so please check anything you rely on against that data.
