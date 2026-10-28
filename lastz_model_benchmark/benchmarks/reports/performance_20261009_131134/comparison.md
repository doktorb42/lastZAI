# Last Z focused performance and reliability benchmark

Run: 2026-10-09T13:11:34+02:00

Synthetic fixtures only: this runner does not import ADB, interact with BlueStacks, take screenshots, or execute game actions.

The action selector is separate from chat generation. `reply_private` is a virtual planner handoff; only validated, normalized chat text can later become executor-only `send_message`.

## Runtime and scope

- Requested backends: cuda
- Contexts: action [1024]; chat [1024]
- Prompt-cache modes: disabled, enabled
- Chat modes: streaming
- Reasoning budget: 0; requested GPU layers: 99
- Historical Vulkan reports remain on disk and are not part of this focused run.

## Production action suite

Qualification is strict: the cold first-hit and every warm request must be 100% valid JSON, schema-valid, legal, and exact. Adversarial cases below do not contribute to this rate.

| Model | Backend | Ctx | Cache | Cold pass | JSON | Schema | Legal | Exact | E2E p50 / p95 | Prompt tok/s p50 | Gen tok/s p50 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | yes | 100.0% | 100.0% | 100.0% | 79.2% | 359.38 / 387.87 ms | 2113.35 | 55.77 | 4990.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | no | 100.0% | 100.0% | 100.0% | 79.2% | 160.57 / 180.80 ms | 1592.71 | 55.76 | 5005.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | disabled | no | 100.0% | 100.0% | 100.0% | 70.8% | 680.83 / 709.28 ms | 1668.24 | 35.61 | 4356.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | enabled | yes | 100.0% | 100.0% | 100.0% | 70.8% | 428.58 / 456.52 ms | 1272.61 | 35.90 | 4366.00 MiB |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | 71.4% | 71.4% | 100.0% | 85.7% | 363.03 / 387.95 ms |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | 78.6% | 78.6% | 100.0% | 85.7% | 163.94 / 196.31 ms |
| qwen3-4b-q4-k-m | cuda | 1024 | disabled | 64.3% | 64.3% | 100.0% | 92.9% | 682.98 / 917.40 ms |
| qwen3-4b-q4-k-m | cuda | 1024 | enabled | 69.0% | 69.0% | 100.0% | 92.9% | 428.38 / 640.20 ms |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | streaming | 28.6% | 0 | 86.22 / 60.71 | 134.08 / 145.66 ms | 57.45 | 556.10 / 744.63 ms | 100.00 / 158.00 | 5010.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | streaming | 28.6% | 0 | 85.65 / 54.50 | 40.98 / 76.23 ms | 57.70 | 453.46 / 628.60 ms | 100.00 / 154.00 | 5012.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | disabled | streaming | 0.0% | 21 | 0.00 / 0.00 | 259.70 / 278.14 ms | 44.11 | 1013.24 / 1911.86 ms | 118.00 / 307.00 | 4369.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | enabled | streaming | 0.0% | 21 | 0.00 / 0.00 | 136.93 / 175.68 ms | 44.08 | 757.73 / 1783.86 ms | 99.00 / 310.00 | 4367.00 MiB |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | selector | 368.88 | 359.38 / 387.87 / 397.71 / 398.36 | n/a | 2113.35 / 2137.66 / 2148.50 / 2152.24 | 55.77 / 57.67 / 58.79 / 58.91 |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | selector | 379.73 | 363.03 / 387.95 / 398.67 / 401.35 | n/a | 2122.83 / 2141.91 / 2149.45 / 2151.53 | 55.62 / 57.10 / 58.01 / 58.37 |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | streaming | 500.51 | 556.10 / 744.63 / 745.55 / 745.78 | 134.08 / 145.66 / 152.13 / 153.75 | 1975.39 / 2098.08 / 2108.93 / 2111.65 | 57.45 / 58.32 / 58.43 / 58.46 |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | selector | 377.29 | 160.57 / 180.80 / 186.31 / 193.83 | n/a | 1592.71 / 1810.02 / 1867.48 / 1883.13 | 55.76 / 57.54 / 57.83 / 58.06 |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | selector | 400.48 | 163.94 / 196.31 / 199.21 / 200.33 | n/a | 1690.13 / 1790.13 / 1877.55 / 1909.48 | 55.97 / 57.80 / 58.16 / 58.32 |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | streaming | 330.86 | 453.46 / 628.60 / 628.91 / 628.99 | 40.98 / 76.23 / 91.76 / 95.64 | 966.42 / 1776.25 / 1805.17 / 1812.40 | 57.70 / 58.27 / 58.62 / 58.71 |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | disabled | selector | 690.61 | 680.83 / 709.28 / 725.49 / 741.01 | n/a | 1668.24 / 1700.85 / 1708.05 / 1709.48 | 35.61 / 36.58 / 36.87 / 37.03 |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | disabled | selector | 694.03 | 682.98 / 917.40 / 939.43 / 944.99 | n/a | 1682.63 / 1701.09 / 1704.66 / 1705.52 | 35.48 / 36.58 / 36.87 / 36.97 |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | disabled | streaming | 1887.58 | 1013.24 / 1911.86 / 1923.30 / 1926.16 | 259.70 / 278.14 / 279.15 / 279.40 | 1609.11 / 1695.22 / 1699.82 / 1700.97 | 44.11 / 44.52 / 44.69 / 44.74 |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | enabled | selector | 690.10 | 428.58 / 456.52 / 469.68 / 472.17 | n/a | 1272.61 / 1489.78 / 1498.89 / 1510.43 | 35.90 / 36.90 / 37.07 / 37.28 |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | enabled | selector | 697.12 | 428.38 / 640.20 / 698.43 / 700.55 | n/a | 1349.77 / 1471.07 / 1508.09 / 1528.34 | 35.72 / 36.62 / 36.75 / 36.81 |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | enabled | streaming | 1732.90 | 757.73 / 1783.86 / 1787.62 / 1788.57 | 136.93 / 175.68 / 190.90 / 194.70 | 760.73 / 956.87 / 1263.15 / 1339.72 | 44.08 / 44.57 / 44.60 / 44.61 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1123.00 | 3188.00 | 3202.00 | 4990.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1124.00 | 3178.00 | 3194.00 | 4998.00 | CUDA0; 37/37; full=yes |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1125.00 | 3176.00 | 3182.00 | 5010.00 | CUDA0; 37/37; full=yes |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1119.00 | 3178.00 | 3187.00 | 5005.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1120.00 | 3169.00 | 3181.00 | 5011.00 | CUDA0; 37/37; full=yes |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1120.00 | 3169.00 | 3180.00 | 5012.00 | CUDA0; 37/37; full=yes |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1124.00 | 3815.00 | 3836.00 | 4356.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1116.00 | 3817.00 | 3834.00 | 4358.00 | CUDA0; 37/37; full=yes |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | 1118.00 | 3820.00 | 3823.00 | 4369.00 | CUDA0; 37/37; full=yes |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1123.00 | 3816.00 | 3826.00 | 4366.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1123.00 | 3820.00 | 3827.00 | 4365.00 | CUDA0; 37/37; full=yes |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | 1119.00 | 3820.00 | 3825.00 | 4367.00 | CUDA0; 37/37; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. withheld: meta-reasoning leakage was detected; fix the player-ready gate/model output before selecting a chat winner

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Player-ready meta-reasoning gate regression contract: 7/7 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
