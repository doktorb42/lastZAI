# Last Z focused performance and reliability benchmark

Run: 2026-10-09T12:55:28+02:00

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

Qualification is strict: 100% valid JSON, schema-valid, legal, and exact decisions. Adversarial cases below do not contribute to this rate.

| Model | Backend | Ctx | Cache | JSON | Schema | Legal | Exact | E2E p50 / p95 | Prompt tok/s p50 | Gen tok/s p50 | VRAM headroom |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | 100.0% | 100.0% | 100.0% | 79.2% | 360.30 / 373.92 ms | 2119.37 | 55.98 | 4994.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | 100.0% | 100.0% | 100.0% | 79.2% | 163.44 / 183.59 ms | 1596.23 | 55.95 | 5007.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | disabled | 100.0% | 100.0% | 100.0% | 70.8% | 685.80 / 709.72 ms | 1668.33 | 35.58 | 4358.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | enabled | 100.0% | 100.0% | 100.0% | 70.8% | 427.20 / 458.23 ms | 1274.27 | 35.55 | 4365.00 MiB |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | 71.4% | 71.4% | 100.0% | 85.7% | 370.04 / 388.34 ms |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | 78.6% | 78.6% | 100.0% | 85.7% | 161.86 / 198.01 ms |
| qwen3-4b-q4-k-m | cuda | 1024 | disabled | 64.3% | 64.3% | 100.0% | 92.9% | 684.71 / 913.04 ms |
| qwen3-4b-q4-k-m | cuda | 1024 | enabled | 69.0% | 69.0% | 100.0% | 92.9% | 429.47 / 656.45 ms |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | streaming | 28.6% | 0 | 86.22 / 60.71 | 136.23 / 148.09 ms | 57.49 | 559.63 / 743.27 ms | 100.00 / 158.00 | 5009.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | streaming | 28.6% | 0 | 85.65 / 54.50 | 46.70 / 75.63 ms | 57.80 | 453.71 / 631.44 ms | 100.00 / 154.00 | 5013.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | disabled | streaming | 0.0% | 21 | 0.00 / 0.00 | 259.20 / 277.39 ms | 43.92 | 1007.87 / 1905.64 ms | 118.00 / 307.00 | 4369.00 MiB |
| qwen3-4b-q4-k-m | cuda | 1024 | enabled | streaming | 0.0% | 21 | 0.00 / 0.00 | 136.28 / 156.32 ms | 44.02 | 768.58 / 1775.56 ms | 99.00 / 310.00 | 4368.00 MiB |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | selector | 390.67 | 360.30 / 373.92 / 388.92 / 397.92 | n/a | 2119.37 / 2148.61 / 2156.59 / 2165.77 | 55.98 / 58.02 / 58.39 / 59.06 |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | selector | 390.08 | 370.04 / 388.34 / 397.35 / 399.52 | n/a | 2119.05 / 2142.91 / 2155.44 / 2160.74 | 55.87 / 57.48 / 58.48 / 58.54 |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | disabled | streaming | 518.42 | 559.63 / 743.27 / 748.51 / 749.82 | 136.23 / 148.09 / 151.11 / 151.86 | 1966.25 / 2049.36 / 2078.64 / 2085.95 | 57.49 / 58.21 / 58.37 / 58.41 |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | selector | 375.75 | 163.44 / 183.59 / 191.91 / 195.28 | n/a | 1596.23 / 1870.42 / 1878.76 / 1880.32 | 55.95 / 57.93 / 59.05 / 59.72 |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | selector | 424.48 | 161.86 / 198.01 / 201.75 / 203.19 | n/a | 1683.31 / 1776.39 / 1825.13 / 1829.83 | 55.65 / 57.65 / 58.31 / 58.61 |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | enabled | streaming | 331.63 | 453.71 / 631.44 / 636.59 / 637.87 | 46.70 / 75.63 / 78.66 / 79.41 | 963.65 / 1771.31 / 1812.04 / 1822.22 | 57.80 / 58.28 / 58.32 / 58.33 |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | disabled | selector | 694.84 | 685.80 / 709.72 / 718.07 / 732.94 | n/a | 1668.33 / 1704.58 / 1710.36 / 1712.05 | 35.58 / 36.76 / 36.97 / 36.99 |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | disabled | selector | 685.77 | 684.71 / 913.04 / 936.44 / 938.27 | n/a | 1681.45 / 1697.65 / 1706.71 / 1711.98 | 35.54 / 36.35 / 36.55 / 36.64 |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | disabled | streaming | 1889.75 | 1007.87 / 1905.64 / 1912.46 / 1914.17 | 259.20 / 277.39 / 278.88 / 279.25 | 1609.45 / 1704.48 / 1714.22 / 1716.66 | 43.92 / 44.61 / 44.78 / 44.82 |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | enabled | selector | 701.82 | 427.20 / 458.23 / 458.77 / 458.88 | n/a | 1274.27 / 1480.09 / 1504.11 / 1510.48 | 35.55 / 36.76 / 36.96 / 37.06 |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | enabled | selector | 697.51 | 429.47 / 656.45 / 702.31 / 703.75 | n/a | 1352.73 / 1452.46 / 1493.67 / 1513.06 | 35.51 / 36.55 / 36.83 / 37.02 |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | enabled | streaming | 1734.81 | 768.58 / 1775.56 / 1785.90 / 1788.49 | 136.28 / 156.32 / 187.31 / 195.05 | 818.89 / 967.07 / 1294.72 / 1376.63 | 44.02 / 44.40 / 44.50 / 44.52 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1124.00 | 3184.00 | 3198.00 | 4994.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1119.00 | 3177.00 | 3194.00 | 4998.00 | CUDA0; 37/37; full=yes |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1125.00 | 3179.00 | 3183.00 | 5009.00 | CUDA0; 37/37; full=yes |
| production_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1124.00 | 3172.00 | 3185.00 | 5007.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1119.00 | 3177.00 | 3185.00 | 5007.00 | CUDA0; 37/37; full=yes |
| chat | qwen2.5-3b-instruct-q4-k-m | cuda | 1024 | 1123.00 | 3177.00 | 3179.00 | 5013.00 | CUDA0; 37/37; full=yes |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1122.00 | 3819.00 | 3834.00 | 4358.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1120.00 | 3813.00 | 3831.00 | 4361.00 | CUDA0; 37/37; full=yes |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | 1121.00 | 3813.00 | 3823.00 | 4369.00 | CUDA0; 37/37; full=yes |
| production_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1120.00 | 3817.00 | 3827.00 | 4365.00 | CUDA0; 37/37; full=yes |
| adversarial_action_suite | qwen3-4b-q4-k-m | cuda | 1024 | 1118.00 | 3819.00 | 3825.00 | 4367.00 | CUDA0; 37/37; full=yes |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | 1124.00 | 3816.00 | 3824.00 | 4368.00 | CUDA0; 37/37; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. withheld: meta-reasoning leakage was detected; fix the player-ready gate/model output before selecting a chat winner

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
