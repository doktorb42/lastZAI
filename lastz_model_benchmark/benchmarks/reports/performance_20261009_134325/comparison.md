# Last Z focused performance and reliability benchmark

Run: 2026-10-09T13:43:25+02:00

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
| ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | disabled | yes | 100.0% | 100.0% | 100.0% | 91.7% | 681.55 / 835.73 ms | 1354.63 | 37.18 | 2494.00 MiB |
| ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | enabled | yes | 100.0% | 100.0% | 100.0% | 87.5% | 347.19 / 505.46 ms | 1158.74 | 37.16 | 2500.00 MiB |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | disabled | 100.0% | 100.0% | 100.0% | 100.0% | 680.78 / 804.72 ms |
| ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | enabled | 100.0% | 100.0% | 100.0% | 100.0% | 346.39 / 488.44 ms |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | disabled | streaming | 42.9% | 0 | 90.81 / 62.50 | 204.28 / 222.22 ms | 37.80 | 1484.15 / 2735.12 ms | 176.00 / 416.00 | 2505.00 MiB |
| ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | enabled | streaming | 71.4% | 0 | 92.22 / 59.00 | 76.47 / 111.01 ms | 37.82 | 1116.06 / 2428.17 ms | 142.00 / 397.00 | 2505.00 MiB |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| production_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | disabled | selector | 682.75 | 681.55 / 835.73 / 838.25 / 839.56 | n/a | 1354.63 / 1379.15 / 1384.88 / 1388.65 | 37.18 / 37.35 / 37.39 / 37.43 |
| adversarial_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | disabled | selector | 695.17 | 680.78 / 804.72 / 809.20 / 809.47 | n/a | 1362.39 / 1380.34 / 1382.74 / 1383.75 | 37.15 / 37.27 / 37.29 / 37.30 |
| chat | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | disabled | streaming | 1491.24 | 1484.15 / 2735.12 / 2739.41 / 2740.49 | 204.28 / 222.22 / 228.53 / 230.10 | 1285.18 / 1361.26 / 1363.56 / 1364.13 | 37.80 / 37.94 / 37.96 / 37.97 |
| production_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | enabled | selector | 833.43 | 347.19 / 505.46 / 513.11 / 521.85 | n/a | 1158.74 / 1231.77 / 1236.93 / 1239.29 | 37.16 / 37.34 / 37.46 / 37.66 |
| adversarial_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | enabled | selector | 809.63 | 346.39 / 488.44 / 493.67 / 496.46 | n/a | 1177.24 / 1238.90 / 1256.91 / 1258.54 | 37.17 / 37.27 / 37.33 / 37.37 |
| chat | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | enabled | streaming | 506.77 | 1116.06 / 2428.17 / 2431.56 / 2432.41 | 76.47 / 111.01 / 118.99 / 120.98 | 604.16 / 1195.41 / 1197.31 / 1197.79 | 37.82 / 37.97 / 37.99 / 38.00 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| production_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | 668.00 | 5679.00 | 5698.00 | 2494.00 | CUDA0; 35/35; full=yes |
| adversarial_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | 666.00 | 5677.00 | 5696.00 | 2496.00 | CUDA0; 35/35; full=yes |
| chat | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | 662.00 | 5682.00 | 5687.00 | 2505.00 | CUDA0; 35/35; full=yes |
| production_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | 668.00 | 5684.00 | 5692.00 | 2500.00 | CUDA0; 35/35; full=yes |
| adversarial_action_suite | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | 667.00 | 5682.00 | 5688.00 | 2504.00 | CUDA0; 35/35; full=yes |
| chat | ministral-3-8b-instruct-2512-q4-k-m | cuda | 1024 | 662.00 | 5682.00 | 5687.00 | 2505.00 | CUDA0; 35/35; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. eligible candidates were ranked by quality, then TTFT and generation speed

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Player-ready meta-reasoning gate regression contract: 7/7 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
