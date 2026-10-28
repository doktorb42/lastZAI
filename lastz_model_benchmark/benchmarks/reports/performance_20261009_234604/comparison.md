# Last Z focused performance and reliability benchmark

Run: 2026-10-09T23:46:04+02:00

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
| gemma-3-4b-it-q4-k-m | cuda | 1024 | disabled | yes | 100.0% | 100.0% | 100.0% | 70.8% | 744.79 / 969.92 ms | 1377.18 | 36.84 | 2171.00 MiB |
| gemma-3-4b-it-q4-k-m | cuda | 1024 | enabled | yes | 100.0% | 100.0% | 100.0% | 73.6% | 536.87 / 846.40 ms | 1649.71 | 45.18 | 2075.00 MiB |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| gemma-3-4b-it-q4-k-m | cuda | 1024 | disabled | 85.7% | 85.7% | 100.0% | 100.0% | 717.55 / 1037.45 ms |
| gemma-3-4b-it-q4-k-m | cuda | 1024 | enabled | 85.7% | 85.7% | 100.0% | 100.0% | 572.06 / 864.53 ms |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| gemma-3-4b-it-q4-k-m | cuda | 1024 | disabled | streaming | 28.6% | 0 | 86.77 / 62.50 | 231.60 / 259.75 ms | 41.23 | 1506.06 / 2828.10 ms | 217.00 / 458.00 | 2160.00 MiB |
| gemma-3-4b-it-q4-k-m | cuda | 1024 | enabled | streaming | 28.6% | 0 | 88.01 / 62.50 | 171.29 / 227.18 ms | 44.36 | 1383.41 / 2351.14 ms | 217.00 / 440.00 | 2238.00 MiB |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| production_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | disabled | selector | 882.80 | 744.79 / 969.92 / 1150.62 / 1171.46 | n/a | 1377.18 / 1512.72 / 1650.97 / 1824.38 | 36.84 / 40.82 / 47.47 / 47.68 |
| adversarial_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | disabled | selector | 738.12 | 717.55 / 1037.45 / 1083.37 / 1092.92 | n/a | 1441.33 / 1489.91 / 1811.49 / 1869.85 | 38.35 / 41.88 / 43.57 / 43.78 |
| chat | gemma-3-4b-it-q4-k-m | cuda | 1024 | disabled | streaming | 1412.95 | 1506.06 / 2828.10 / 2840.96 / 2844.18 | 231.60 / 259.75 / 260.49 / 260.67 | 1276.65 / 1382.34 / 1422.12 / 1432.07 | 41.23 / 45.66 / 46.27 / 46.42 |
| production_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | enabled | selector | 652.41 | 536.87 / 846.40 / 914.91 / 917.96 | n/a | 1649.71 / 1766.29 / 1821.85 / 1864.64 | 45.18 / 47.29 / 48.99 / 51.28 |
| adversarial_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | enabled | selector | 663.63 | 572.06 / 864.53 / 885.58 / 891.00 | n/a | 1586.01 / 1636.69 / 1644.32 / 1645.04 | 42.52 / 44.85 / 45.40 / 45.67 |
| chat | gemma-3-4b-it-q4-k-m | cuda | 1024 | enabled | streaming | 603.43 | 1383.41 / 2351.14 / 2392.22 / 2402.49 | 171.29 / 227.18 / 228.18 / 228.43 | 1399.62 / 1458.86 / 1468.56 / 1470.98 | 44.36 / 45.22 / 45.33 / 45.35 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| production_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | 3196.00 | 5911.00 | 6021.00 | 2171.00 | CUDA0; 35/35; full=yes |
| adversarial_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | 3307.00 | 6012.00 | 6020.00 | 2172.00 | CUDA0; 35/35; full=yes |
| chat | gemma-3-4b-it-q4-k-m | cuda | 1024 | 3259.00 | 5948.00 | 6032.00 | 2160.00 | CUDA0; 35/35; full=yes |
| production_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | 3349.00 | 6034.00 | 6117.00 | 2075.00 | CUDA0; 35/35; full=yes |
| adversarial_action_suite | gemma-3-4b-it-q4-k-m | cuda | 1024 | 3369.00 | 6048.00 | 6052.00 | 2140.00 | CUDA0; 35/35; full=yes |
| chat | gemma-3-4b-it-q4-k-m | cuda | 1024 | 3266.00 | 5943.00 | 5954.00 | 2238.00 | CUDA0; 35/35; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. eligible candidates were ranked by quality, then TTFT and generation speed

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Player-ready meta-reasoning gate regression contract: 7/7 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
