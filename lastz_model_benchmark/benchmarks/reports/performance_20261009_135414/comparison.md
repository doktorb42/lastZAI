# Last Z focused performance and reliability benchmark

Run: 2026-10-09T13:54:14+02:00

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
| qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | disabled | yes | 100.0% | 100.0% | 100.0% | 91.7% | 484.87 / 542.02 ms | 1553.16 | 41.37 | 3042.00 MiB |
| qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | enabled | yes | 100.0% | 100.0% | 100.0% | 94.4% | 214.80 / 312.07 ms | 1242.69 | 41.37 | 3049.00 MiB |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | disabled | 92.9% | 92.9% | 100.0% | 100.0% | 489.62 / 632.64 ms |
| qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | enabled | 92.9% | 92.9% | 100.0% | 100.0% | 210.18 / 376.01 ms |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | disabled | streaming | 42.9% | 0 | 88.67 / 65.50 | 174.30 / 186.24 ms | 42.10 | 801.66 / 1459.91 ms | 101.00 / 235.00 | 3058.00 MiB |
| qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | enabled | streaming | 42.9% | 0 | 89.22 / 53.00 | 50.12 / 97.48 ms | 42.15 | 706.78 / 1322.42 ms | 101.00 / 248.00 | 3058.00 MiB |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| production_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | disabled | selector | 491.25 | 484.87 / 542.02 / 637.51 / 645.98 | n/a | 1553.16 / 1576.00 / 1586.27 / 1591.08 | 41.37 / 41.69 / 41.86 / 41.90 |
| adversarial_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | disabled | selector | 496.50 | 489.62 / 632.64 / 640.14 / 640.77 | n/a | 1560.43 / 1574.80 / 1579.35 / 1579.98 | 41.31 / 41.70 / 41.87 / 41.88 |
| chat | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | disabled | streaming | 834.47 | 801.66 / 1459.91 / 1476.36 / 1480.47 | 174.30 / 186.24 / 195.22 / 197.47 | 1497.50 / 1585.91 / 1587.86 / 1588.34 | 42.10 / 42.23 / 42.27 / 42.28 |
| production_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | enabled | selector | 523.08 | 214.80 / 312.07 / 359.97 / 362.56 | n/a | 1242.69 / 1408.77 / 1433.72 / 1453.17 | 41.37 / 41.66 / 42.07 / 42.42 |
| adversarial_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | enabled | selector | 649.39 | 210.18 / 376.01 / 384.92 / 385.94 | n/a | 1328.92 / 1400.39 / 1453.68 / 1454.66 | 41.34 / 41.53 / 41.77 / 41.83 |
| chat | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | enabled | streaming | 409.72 | 706.78 / 1322.42 / 1329.06 / 1330.72 | 50.12 / 97.48 / 98.16 / 98.33 | 730.68 / 1375.64 / 1400.08 / 1406.19 | 42.15 / 42.30 / 42.44 / 42.47 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| production_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | 668.00 | 5138.00 | 5150.00 | 3042.00 | CUDA0; 29/29; full=yes |
| adversarial_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | 666.00 | 5125.00 | 5148.00 | 3044.00 | CUDA0; 29/29; full=yes |
| chat | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | 667.00 | 5129.00 | 5134.00 | 3058.00 | CUDA0; 29/29; full=yes |
| production_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | 662.00 | 5130.00 | 5143.00 | 3049.00 | CUDA0; 29/29; full=yes |
| adversarial_action_suite | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | 668.00 | 5125.00 | 5140.00 | 3052.00 | CUDA0; 29/29; full=yes |
| chat | qwen2.5-7b-instruct-q4-k-m | cuda | 1024 | 667.00 | 5125.00 | 5134.00 | 3058.00 | CUDA0; 29/29; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. eligible candidates were ranked by quality, then TTFT and generation speed

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Player-ready meta-reasoning gate regression contract: 7/7 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
