# Goedel-Prover-V2-8B benchmark summary

## 1. Main results (stage=main)

| config | decode tok/s | short-prompt TTFT s | prompt tok/s (long) | first load s | later loads s |
|---|---|---|---|---|---|
| A-OV-CPU | 7.83 (7.74..7.85) n=9 | 1.217 (1.153..1.498) n=9 | 187.7 (159.7..198.2) n=6 | 5.45 (round 0) | 6.33 (6.24..6.42) n=2 |
| A-OV-iGPU | 6.70 (6.63..6.76) n=9 | 2.785 (2.714..2.892) n=9 | 134.9 (134.0..135.5) n=6 | 14.33 (round 0) | 2.90 (2.52..3.27) n=2 |
| A-OV-NPU | 5.59 (5.56..5.60) n=9 | 4.772 (4.736..4.812) n=9 | 203.7 (196.8..204.4) n=6 | 9.30 (round 0) | 5.86 (5.84..5.87) n=2 |
| B-llama-CPU | 7.14 (6.99..7.20) n=9 | 1.454 (1.399..1.484) n=9 | 158.3 (155.5..160.7) n=6 | 4.18 (round 0) | 3.13 (3.13..3.14) n=2 |
| B-llama-iGPU | 5.54 (4.61..5.57) n=9 | 2.591 (2.559..2.891) n=9 | 92.1 (91.2..92.3) n=6 | 3.95 (round 0) | 2.88 (2.88..2.89) n=2 |

Cells are median (min..max) n=samples, pooled over rounds and repetitions. Prompt rate = prompt_tokens / ttft_s on the long prompt (TTFT includes the first generated token). The first load is the one with the lowest round; it may be a cold compile, while later loads should come from the cache.

## 2. Energy (package power, short-prompt windows, stage=main)

| config | windows used | windows excluded | package W | net W | J/token gross | J/token net | mean idle W |
|---|---|---|---|---|---|---|---|
| A-OV-CPU | 9 | 0 | 99.4 (96.0..101.1) | 77.1 (74.2..79.1) | 13.125 (12.842..13.343) | 10.208 (9.925..10.447) | 22.0 |
| A-OV-iGPU | 9 | 0 | 39.4 (39.0..39.8) | 17.4 (17.0..19.6) | 6.313 (6.206..6.393) | 2.794 (2.708..3.121) | 21.5 |
| A-OV-NPU | 9 | 0 | 25.9 (24.8..27.4) | 3.7 (3.6..6.4) | 5.102 (4.891..5.399) | 0.741 (0.703..1.264) | 21.9 |
| B-llama-CPU | 9 | 0 | 105.2 (103.7..107.4) | 83.0 (81.4..86.2) | 15.362 (15.144..15.584) | 12.113 (11.914..12.419) | 21.9 |
| B-llama-iGPU | 9 | 0 | 32.4 (31.8..33.4) | 12.4 (10.0..14.4) | 6.177 (6.136..7.270) | 2.361 (2.253..2.724) | 20.0 |

Cells are median (min..max) over the used windows. Joules = trapezoid integral of `rapl_package0_pkg` over the run's [start, end] (prefill included), interpolated to the window edges from the neighbouring samples when those exist. Net = minus the mean power of the idle record of the same config and round (J/token net subtracts idle W x window seconds). J/token divides by `generated`. Windows with fewer than 3 samples, a gap over 5 s, or under 95% coverage are excluded. Net values are n/a when the session has no usable idle record.

## 3. Device verification (short-prompt windows, stage=main)

| config | samples | mean cpu_time % | mean cpu_mhz | top 3 engines (mean %) |
|---|---|---|---|---|
| A-OV-CPU | 305 | 88.1 | 4813 | 0x00000000_0x0000F2EA:3D 0.6% |
| A-OV-iGPU | 367 | 11.0 | 4943 | 0x00000000_0x0000F2EA:Compute 99.4%; 0x00000000_0x0000F2EA:3D 0.3% |
| A-OV-NPU | 453 | 6.4 | 4525 | 0x00000000_0x0000F6FC:3D 98.9%; 0x00000000_0x0000F2EA:3D 0.4% |
| B-llama-CPU | 334 | 100.0 | 4807 | 0x00000000_0x0000F2EA:3D 0.6% |
| B-llama-iGPU | 465 | 7.5 | 4666 | 0x00000000_0x0000F2EA:Compute 99.4%; 0x00000000_0x0000F2EA:3D 0.2% |

Engine keys are `<luid>:<engtype>`. The mean is over all samples in the windows; a sample where the key was omitted (under 0.5%) counts as 0.

## 4. Consistency checks

- PASS: generated across configs (main/short): all 45 runs = 256
- PASS: generated across configs (main/long): all 30 runs = 16
- PASS: generated across configs (sweep/short): all 16 runs = 96
- PASS: prompt_tokens across configs (main/short): all 45 runs = 229
- PASS: prompt_tokens across configs (main/long): all 30 runs = 974
- PASS: prompt_tokens across configs (sweep/short): all 16 runs = 229
- PASS: every short/long run finished with finish=length
- PASS: token_sha identical across reps within each session (38 sessions)
- PASS: token_sha identical across rounds within each config (fresh process each)
- WARN: first_tokens agree between all configs: diverge at A-OV-CPU vs A-OV-iGPU: 34 of 64; A-OV-CPU vs B-llama-CPU: 15 of 64; A-OV-CPU vs B-llama-iGPU: 15 of 64; A-OV-iGPU vs B-llama-CPU: 15 of 64; A-OV-iGPU vs B-llama-iGPU: 15 of 64; A-OV-NPU vs A-OV-iGPU: 34 of 64; A-OV-NPU vs B-llama-CPU: 15 of 64; A-OV-NPU vs B-llama-iGPU: 15 of 64 (expected between different quantizations or devices; same IR on different devices should agree longer)

Shared prefix length of `first_tokens` (first short run per config, stage=main; diagonal = tokens recorded):

|  | A-OV-CPU | A-OV-iGPU | A-OV-NPU | B-llama-CPU | B-llama-iGPU |
|---|---|---|---|---|---|
| A-OV-CPU | 64 | 34 | 64 | 15 | 15 |
| A-OV-iGPU | 34 | 64 | 34 | 15 | 15 |
| A-OV-NPU | 64 | 34 | 64 | 15 | 15 |
| B-llama-CPU | 15 | 15 | 15 | 64 | 64 |
| B-llama-iGPU | 15 | 15 | 15 | 64 | 64 |

- PASS: drift A-OV-CPU: round 0: 7.78, round 1: 7.83, round 2: 7.83; max/min spread 0.7% (limit 5%)
- PASS: drift A-OV-iGPU: round 0: 6.65, round 1: 6.70, round 2: 6.74; max/min spread 1.3% (limit 5%)
- PASS: drift A-OV-NPU: round 0: 5.59, round 1: 5.59, round 2: 5.59; max/min spread 0.1% (limit 5%)
- PASS: drift B-llama-CPU: round 0: 7.08, round 1: 7.14, round 2: 7.07; max/min spread 1.1% (limit 5%)
- WARN: drift B-llama-iGPU: round 0: 4.61, round 1: 5.54, round 2: 5.55; max/min spread 20.6% (limit 5%)
- PASS: no failed sessions

## 5. Thread sweep (stage=sweep)

| config | runtime | threads | decode tok/s | short TTFT s | load s |
|---|---|---|---|---|---|
| llama-cpu-t8 | llama-cpu | 8 | 6.33 (6.10..6.56) n=2 | 2.033 (2.028..2.037) n=2 | 3.41 (3.41..3.41) n=1 |
| llama-cpu-t14 | llama-cpu | 14 | 6.50 (6.47..6.53) n=2 | 1.854 (1.843..1.865) n=2 | 3.15 (3.15..3.15) n=1 |
| llama-cpu-t19 | llama-cpu | 19 | 6.42 (6.23..6.62) n=2 | 1.501 (1.479..1.524) n=2 | 3.14 (3.14..3.14) n=1 |
| llama-cpu-t20 | llama-cpu | 20 | 6.66 (6.59..6.72) n=2 | 1.440 (1.424..1.455) n=2 | 3.15 (3.15..3.15) n=1 |
| ov-cpu-tauto | ov-cpu | auto | 7.24 (7.20..7.27) n=2 | 1.529 (1.519..1.540) n=2 | 5.55 (5.55..5.55) n=1 |
| ov-cpu-t8 | ov-cpu | 8 | 7.31 (7.23..7.38) n=2 | 1.535 (1.517..1.554) n=2 | 6.54 (6.54..6.54) n=1 |
| ov-cpu-t14 | ov-cpu | 14 | 7.58 (7.40..7.77) n=2 | 1.882 (1.871..1.892) n=2 | 6.44 (6.44..6.44) n=1 |
| ov-cpu-t20 | ov-cpu | 20 | 7.75 (7.63..7.88) n=2 | 1.411 (1.407..1.415) n=2 | 6.14 (6.14..6.14) n=1 |

- Best thread count for llama-cpu: 20 (6.66 tok/s, llama-cpu-t20)
- Best thread count for ov-cpu: 20 (7.75 tok/s, ov-cpu-t20)

## 6. Proof attempts (prove.jsonl)

| lemma | A-OV-CPU | A-OV-iGPU | A-OV-NPU | B-llama-CPU | B-llama-iGPU |
|---|---|---|---|---|---|
| task_a | PROVED (1170 tok, 7.3 tok/s, 164 s) | PROVED (1347 tok, 6.7 tok/s, 204 s) | PROVED (1632 tok, 5.6 tok/s, 296 s) | PROVED (1045 tok, 6.8 tok/s, 154 s) | PROVED (945 tok, 5.0 tok/s, 190 s) |
| task_b | PROVED (2175 tok, 7.6 tok/s, 287 s) | PROVED (2130 tok, 6.5 tok/s, 329 s) | truncated (3000 tok, 5.5 tok/s, 550 s) | PROVED (1845 tok, 6.8 tok/s, 272 s) | PROVED (2029 tok, 4.8 tok/s, 422 s) |
| task_c | forbidden_word (3000 tok, 7.5 tok/s, 400 s) | forbidden_word (3000 tok, 6.4 tok/s, 470 s) | truncated (3000 tok, 5.5 tok/s, 546 s) | PROVED (2703 tok, 6.7 tok/s, 403 s) | compile_error (2591 tok, 4.9 tok/s, 538 s) |

Cells: verdict (generated tokens, decode tok/s, total seconds); several attempts for one cell are separated by semicolons.
