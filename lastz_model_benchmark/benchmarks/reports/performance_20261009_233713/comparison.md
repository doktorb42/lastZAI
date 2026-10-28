# Last Z focused performance and reliability benchmark

Run: 2026-10-09T23:37:13+02:00

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
| hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | disabled | yes | 100.0% | 100.0% | 100.0% | 79.2% | 852.88 / 1052.14 ms | 911.92 | 22.84 | 195.00 MiB |
| hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | enabled | yes | 100.0% | 100.0% | 100.0% | 79.2% | 370.15 / 607.74 ms | 723.16 | 23.11 | 269.00 MiB |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | disabled | 92.9% | 92.9% | 100.0% | 92.9% | 840.38 / 884.10 ms |
| hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | enabled | 92.9% | 92.9% | 100.0% | 92.9% | 392.87 / 495.85 ms |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | disabled | streaming | 57.1% | 0 | 88.99 / 48.93 | 281.27 / 318.63 ms | 24.73 | 1788.10 / 2708.59 ms | 134.00 / 275.00 | 242.00 MiB |
| hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | enabled | streaming | 66.7% | 0 | 89.82 / 57.93 | 93.98 / 158.80 ms | 23.65 | 1588.43 / 2922.00 ms | 135.00 / 283.00 | 301.00 MiB |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| production_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | disabled | selector | 1046.96 | 852.88 / 1052.14 / 1183.50 / 1192.20 | n/a | 911.92 / 1069.34 / 1094.49 / 1134.55 | 22.84 / 28.36 / 29.07 / 29.33 |
| adversarial_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | disabled | selector | 799.02 | 840.38 / 884.10 / 914.06 / 917.42 | n/a | 906.76 / 966.08 / 985.50 / 988.06 | 23.70 / 25.25 / 27.94 / 29.51 |
| chat | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | disabled | streaming | 1782.95 | 1788.10 / 2708.59 / 2751.29 / 2761.96 | 281.27 / 318.63 / 339.89 / 345.21 | 868.86 / 928.95 / 946.76 / 951.21 | 24.73 / 25.84 / 26.75 / 26.98 |
| production_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | enabled | selector | 812.28 | 370.15 / 607.74 / 661.97 / 685.25 | n/a | 723.16 / 1017.02 / 1052.07 / 1082.49 | 23.11 / 29.88 / 31.78 / 31.89 |
| adversarial_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | enabled | selector | 2318.91 | 392.87 / 495.85 / 533.23 / 537.41 | n/a | 721.03 / 867.93 / 901.84 / 902.79 | 21.42 / 24.31 / 28.65 / 28.73 |
| chat | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | enabled | streaming | 517.89 | 1588.43 / 2922.00 / 2968.34 / 2979.92 | 93.98 / 158.80 / 172.99 / 176.54 | 397.20 / 809.38 / 827.62 / 832.18 | 23.65 / 25.74 / 26.30 / 26.43 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| production_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | 4148.00 | 7912.00 | 7997.00 | 195.00 | CUDA0; 33/33; full=yes |
| adversarial_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | 3286.00 | 7915.00 | 7985.00 | 207.00 | CUDA0; 33/33; full=yes |
| chat | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | 3191.00 | 7939.00 | 7950.00 | 242.00 | CUDA0; 33/33; full=yes |
| production_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | 3164.00 | 7807.00 | 7923.00 | 269.00 | CUDA0; 33/33; full=yes |
| adversarial_action_suite | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | 3211.00 | 7869.00 | 7948.00 | 244.00 | CUDA0; 33/33; full=yes |
| chat | hermes-3-llama-3.1-8b-q4-k-m | cuda | 1024 | 2963.00 | 7697.00 | 7891.00 | 301.00 | CUDA0; 33/33; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. eligible candidates were ranked by quality, then TTFT and generation speed

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Player-ready meta-reasoning gate regression contract: 7/7 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
