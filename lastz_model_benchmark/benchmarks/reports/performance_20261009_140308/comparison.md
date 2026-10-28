# Last Z focused performance and reliability benchmark

Run: 2026-10-09T14:03:08+02:00

Synthetic fixtures only: this runner does not import ADB, interact with BlueStacks, take screenshots, or execute game actions.

The action selector is separate from chat generation. `reply_private` is a virtual planner handoff; only validated, normalized chat text can later become executor-only `send_message`.

## Runtime and scope

- Requested backends: cuda
- Contexts: action [1024]; chat [1024, 2048, 4096]
- Prompt-cache modes: enabled
- Chat modes: streaming, non_streaming
- Reasoning budget: 0; requested GPU layers: 99
- Historical Vulkan reports remain on disk and are not part of this focused run.

## Production action suite

Qualification is strict: the cold first-hit and every warm request must be 100% valid JSON, schema-valid, legal, and exact. Adversarial cases below do not contribute to this rate.

| Model | Backend | Ctx | Cache | Cold pass | JSON | Schema | Legal | Exact | E2E p50 / p95 | Prompt tok/s p50 | Gen tok/s p50 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| phi-4-mini-instruct-q4-k-m | cuda | 1024 | enabled | yes | 100.0% | 100.0% | 100.0% | 83.3% | 187.84 / 217.06 ms | 2084.21 | 67.95 | 4851.00 MiB |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| No completed adversarial action configuration | - | - | - | - | - | - | - | - |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| No completed chat configuration | - | - | - | - | - | - | - | - | - | - | - | - |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| production_action_suite | phi-4-mini-instruct-q4-k-m | cuda | 1024 | enabled | selector | 447.05 | 187.84 / 217.06 / 219.94 / 220.37 | n/a | 2084.21 / 2398.55 / 2443.70 / 2451.73 | 67.95 / 68.22 / 68.34 / 68.38 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| production_action_suite | phi-4-mini-instruct-q4-k-m | cuda | 1024 | 667.00 | 3334.00 | 3341.00 | 4851.00 | CUDA0; 33/33; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. eligible candidates were ranked by quality, then TTFT and generation speed

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Player-ready meta-reasoning gate regression contract: 7/7 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
