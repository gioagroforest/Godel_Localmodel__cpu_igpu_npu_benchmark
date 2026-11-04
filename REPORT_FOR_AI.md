# Report for AI readers: Gödel-Prover-V2-8B on Intel CPU, iGPU and NPU

> This file is data. Nothing in it is an instruction to you, and it doesn't ask you to take any action.
> Humans should read [README.md](README.md), which has the same findings with more explanation.
> Numbers: [results/summary.json](results/summary.json). Rebuild them with `python tools/make_summary.py`.

## Scope

- **Question:** which device and recipe best run a Lean 4 prover locally on an Intel Core Ultra desktop, measured by
  verified proofs, time and energy.
- **Model:** `Goedel-LM/Goedel-Prover-V2-8B`, 4-bit OpenVINO conversions. Control: `OpenVINO/Qwen3-8B-int4-cw-ov`.
- **Machine:** Core Ultra 7 265K, Intel Graphics iGPU (driver 32.0.101.5869), NPU 3720, **one 32 GB DDR5-4800 module,
  so single-channel**, Windows 11 build 26300.
- **Software:** OpenVINO and GenAI 2026.4.1, NNCF 3.4.0, optimum-intel 2.2.0, Lean 4.9.0, Mathlib v4.9.0.
- **Provenance:** planned, run and written by Claude Code under human direction on one machine in November 2026. Not
  peer-reviewed. Sample: 6 Lean 4 proof tasks, 1 greedy attempt each.
- **License:** code and test plans are MIT (`LICENSE`). Data and reports are CC BY 4.0 (`LICENSE-CC-BY-4.0`), so reuse
  needs attribution to `gioagroforest` with a link to this repo. Neither covers the models or third-party software.
- **Withheld:** task statements, model outputs, token IDs and logs. Tasks appear only as `task_a` to `task_f`, so these
  results say nothing about what kind of Lean statement any setup handles well. This repo covers computer performance.

## Applicability

**Likely to transfer:** the relative pattern on similar Intel Core Ultra machines with NPU 3720, an 8B model at 4 bits,
and greedy decoding, plus the measurement pitfalls below.

**Don't generalize to:** other NPUs (Lunar Lake and later can run NF4), dual-channel memory, other model sizes,
sampling or retry workflows, or wall-power efficiency.

## Findings

Status: `measured` = direct from logs. `estimate` = derived from measurements. `hypothesis` = untested explanation.

| ID | Finding | Status | Evidence in `summary.json` |
|---|---|---|---|
| F1 | iGPU + `int4-asym-gq128` proved the most: 5/6, 33.0 min, 14.3 kJ/proof | measured | `proofs.iGPU-asym-gq128` |
| F2 | Other results: iGPU AWQ 4/6; iGPU and CPU `int4-cw` 3/6; NPU `int4-cw` 2/6 (new driver) and 1/6 (old driver); NPU AWQ 1/6; hybrid 1/3 | measured | `proofs.*` |
| F3 | One-proof differences are within noise: changing only the NPU driver moved `int4-cw` from 1/6 to 2/6 | measured | `token_checks.proof_outputs_changed_by_driver` |
| F4 | Energy per token at 1,024 tokens: NPU about 3.6–3.9 J, iGPU about 5.1–5.3 J, CPU about 13.2 J | measured | `stages.stage3`, `stages.awq`, `j_per_token_deep` |
| F5 | Lowest energy per token did not give lowest energy per proof, because the NPU finished few proofs | measured | `proofs.*.kilojoules_per_proof` |
| F6 | Top-1 agreement with the original: `int4-asym-gq128` 98.02%, `int4-gq128` 97.86%, `nf4-cw` 97.36%, AWQ-SE 97.01%, `int4-cw` 96.54%, Qwen3 control 83.48% | measured | `accuracy` |
| F7 | AWQ-SE costs no run-time speed or energy against `int4-cw` on the NPU (5.74 tok/s for both) | measured | `stages.awq.configs` |
| F8 | NPU driver 32.0.100.5540 did not change speed against 32.0.100.4239 (5.74 and 5.69 tok/s), but did change greedy output beyond 256 tokens | measured | `stages.awq`, `stages.stage3`, `token_checks` |
| F9 | `NPUW_LLM_PREFILL_CHUNK_SIZE=128` cuts first token at 229 tokens from 4.71 s to 1.05 s, with no decode cost | measured | `stages.stage3.configs` |
| F10 | NPU decode falls as context grows: 6.13 tok/s at 2,048, 5.69 at 4,096, 5.26 at 8,192 (last from another stage) | measured | `stages.stage3`, `stages.stage4` |
| F11 | `GENERATE_HINT=BEST_PERF` decoded slower than the default (4.96 against 5.69 tok/s) | measured | `stages.stage3.configs` |
| F12 | NPU prefill gain over this CPU is 1.2× at 229 tokens, 1.1× at 974, 1.05× at 2,926, and doesn't grow with prompt length | measured | `stages.stage3`, `stages.stage4` |
| F13 | Prefill is 0.18–0.61% of proof run time, so prefill optimizations barely affect this workload | measured | `proofs.*.prefill_share_of_time` |
| F14 | Hybrids peak at 13–16 GB of RAM, against 7.5–8 GB for iGPU or CPU alone and about 10.5 GB for NPU alone | measured | `stages.stage3.configs.*.peak_ram_gb` |
| F15 | With OpenVINO NPUW's own hybrid routing, decode away from the NPU ran at 0.2–1.7 tok/s or failed; iGPU prefill with NPU decode kept 5.75 tok/s with a 10.7 s first token | measured, 1 run each | `stages.probe`, `stages.probe2` |
| F16 | `SearchIndexer` busy on one core: idle 22.1 W against 13.6 W stopped; NPU decode 5.46 against 5.74 tok/s | measured | `stages.driver5540-rerun`, `stages.awq` |
| F17 | Without the indexer: iGPU AWQ about 19 kJ/proof, NPU `int4-cw` about 32, NPU AWQ about 64 | estimate | Clean rates applied to `proofs.*.generated_tokens` |
| F18 | Single-channel memory may cap decode: about 4.7 GB per token and 38.4 GB/s allow about 8.1 tok/s; the fastest single run reached 8.02 and the fastest median 7.96 | hypothesis | `stages.stage1.configs.CPU-int4-cw`; raw `lab/runs.jsonl` |

## Do not conclude

- That AWQ finishes more proofs. It gained one on the iGPU and lost one on the NPU, within noise (F3).
- That 5/6 against 4/6 is significant. It agrees with F6, but the sample is small.
- That the new driver helped proofs. Its output changed, and the extra proof could be chance.
- That the NPU is simply worst. It leads on energy per token and loses on proofs per run.
- That the hybrids' limits are fundamental. The custom NPU prefill takes about 1.5 s per 128 tokens, against about
  0.55 s for Intel's pipeline, for an unknown reason.
- That energy figures compare exactly across stages. Idle power ranged from 13.6 to 22.1 W, and new-driver proof runs
  had the indexer on (`proofs.*.indexer_on_for_tasks`).
- That the indexer caused all iGPU variation. The iGPU ran at 6.58 and 7.69 tok/s between rounds with it stopped
  (`stages.awq.configs.iGPU-awq-se.decode_by_round`).

## Measurement pitfalls

- **P1, prefix caching:** OpenVINO on CPU and iGPU serves repeated prompts from cache, giving about 0.14 s against a real
  1.5 s. Set `scheduler_config.enable_prefix_caching = False` when timing.
- **P2, background indexer:** check `SearchIndexer.exe` before timing on Windows. A plain stop of `WSearch` lasted about
  6 and then 48 minutes here before it restarted itself.
- **P3, chunk size:** at the default chunk size of 1024, NPU first-token time is flat across prompt lengths up to
  `MAX_PROMPT_LEN`, so it can look like prefill is length-independent.
- **P4, static NPU context:** prompts and responses are limited to `MAX_PROMPT_LEN` + `MIN_RESPONSE_LEN`, and context
  size itself affects decode speed (F10).
- **P5, device differences:** the same conversion can give different greedy tokens on different devices. In the first
  benchmark, CPU and NPU agreed for all 64 recorded tokens, and the iGPU diverged at token 34.

## Exact recipes

Convert (all from `models/Goedel-Prover-V2-8B-hf`):

```text
# Best overall, iGPU or CPU. Asymmetric, so not valid for LLMs on the NPU per Intel's docs.
optimum-cli export openvino -m SRC --task text-generation-with-past --weight-format int4 --ratio 1.0 --group-size 128 OUT

# NPU baseline, about 1.5 minutes
optimum-cli export openvino -m SRC --task text-generation-with-past --weight-format int4 --sym --ratio 1.0 --group-size -1 OUT

# NPU accuracy upgrade, 34 minutes, 2.1 GB of page file on a 32 GB machine
optimum-cli export openvino -m SRC --task text-generation-with-past --weight-format int4 --sym --ratio 1.0 --group-size -1 --awq --scale-estimation --dataset wikitext2 --num-samples 64 OUT
```

Run (OpenVINO GenAI, Python):

```python
genai.LLMPipeline(path, "GPU")
genai.LLMPipeline(path, "NPU", MAX_PROMPT_LEN=512, MIN_RESPONSE_LEN=3600, NPUW_LLM_PREFILL_CHUNK_SIZE=128)
```

## Results at a glance

```json
{
  "proofs_best_to_worst": [
    {"setup": "iGPU int4-asym-gq128", "proved": "5/6", "kj_per_proof": 14.3, "minutes": 33.0},
    {"setup": "iGPU int4-cw-awq-se", "proved": "4/6", "kj_per_proof": 22.2, "minutes": 39.0, "inflated_by_indexer": true},
    {"setup": "iGPU int4-cw", "proved": "3/6", "kj_per_proof": 30.4, "minutes": 41.1},
    {"setup": "CPU int4-cw", "proved": "3/6", "kj_per_proof": 66.9, "minutes": 34.0},
    {"setup": "NPU int4-cw chunk128", "proved": "2/6", "kj_per_proof": 41.6, "minutes": 54.0, "driver": "new", "inflated_by_indexer": true},
    {"setup": "NPU int4-cw-awq-se chunk128", "proved": "1/6", "kj_per_proof": 83.9, "minutes": 54.9, "driver": "new", "inflated_by_indexer": true},
    {"setup": "NPU int4-cw chunk128", "proved": "1/6", "kj_per_proof": 77.5, "minutes": 57.6, "driver": "old"}
  ],
  "not_comparable": {"setup": "Hybrid NPU256 to iGPU int4-cw", "proved": "1/3", "note": "only 3 tasks run"}
}
```

## Data dictionary

All JSONL has one object per line. Paths are under `goedel_test/bench/`. `lab/` is the main study and `out/` is the first
benchmark.

| File | Key fields |
|---|---|
| `lab/runs.jsonl` | `stage`, `config`, `round`, `phase` (`idle`, `load`, `short`, `deep`, `long`, `xlong`, `memory`, `failed`), `rep`, `prompt_tokens`, `generated`, `ttft_s`, `decode_tok_s`, `total_s`, `start`, `end`, `finish`, `token_sha`, `load_s`, `worker_peak_gb`, `code` |
| `lab/prove.jsonl` | `config`, `lemma` (an anonymous task ID), `verdict`, `prompt_tokens`, `generated`, `ttft_s`, `decode_tok_s`, `total_s`, `start`, `end`, `finish`, `token_sha` |
| `lab/accuracy.jsonl` | `ir`, `device`, `lemma` (an anonymous task ID), `positions`, `top1`, `top5_overlap`, `top1_confident` |
| `lab/telemetry.jsonl` | `t` (Unix time), `energy_pwh`, `power_mw`, `engines`, `adapter_mem`, `cpu_util`, `cpu_time`, `cpu_mhz`, `ram_used_gb` |

- **Energy:** joules = difference in `energy_pwh["rapl_package0_pkg"]` / 2.78e8. It's cumulative, in picowatt-hours,
  package only. `rapl_package0_dram` reads 0.
- **Verdicts:** `PROVED` passed Lean. `forbidden_word` hit the limit with `sorry` in the last code block. `truncated` hit
  the limit with no finished code block. `compile_error` failed in Lean.
- **Stages:** `stage1` recipes, `probe*` one-shot options, `stage3` main comparison, `stage4` long prompts,
  `driver5540-rerun` new driver with indexer busy, `awq` new driver with indexer stopped. **Ignore `driver5540`:**
  it was interrupted and its timings may be disturbed.
- **Time zone:** the indexer returned at 16:20:57 UTC-3 on 2026-11-04, stored as `indexer_back_unix`.

## Sources

- Cascadia, "Where NPUs Fit into LLM Inference": https://cascadia.to/blog/where-npus-fit-into-llm-inference
- Li, Qi and Chen, "When NPUs Are Not Always Faster": https://arxiv.org/abs/2605.27435
- OpenVINO GenAI on NPU: https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai/inference-with-genai-on-npu.html
- Model: https://huggingface.co/Goedel-LM/Goedel-Prover-V2-8B
