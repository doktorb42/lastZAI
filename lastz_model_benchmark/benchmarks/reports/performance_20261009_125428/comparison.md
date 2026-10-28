# Last Z focused performance and reliability benchmark

Run: 2026-10-09T12:54:28+02:00

Synthetic fixtures only: this runner does not import ADB, interact with BlueStacks, take screenshots, or execute game actions.

The action selector is separate from chat generation. `reply_private` is a virtual planner handoff; only validated, normalized chat text can later become executor-only `send_message`.

## Runtime and scope

- Requested backends: cuda
- Contexts: action [1024, 2048, 4096]; chat [1024]
- Prompt-cache modes: disabled
- Chat modes: streaming
- Reasoning budget: 0; requested GPU layers: 99
- Historical Vulkan reports remain on disk and are not part of this focused run.

## Production action suite

Qualification is strict: 100% valid JSON, schema-valid, legal, and exact decisions. Adversarial cases below do not contribute to this rate.

| Model | Backend | Ctx | Cache | JSON | Schema | Legal | Exact | E2E p50 / p95 | Prompt tok/s p50 | Gen tok/s p50 | VRAM headroom |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| No completed production action configuration | - | - | - | - | - | - | - | - | - | - | - |

## Adversarial action suite

This is a separate robustness score for tempting, unavailable, injected, or screen-invalid choices. It is never mixed into production qualification.

| Model | Backend | Ctx | Cache | Robustness exact | Safe wait | Schema | Legal | E2E p50 / p95 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| No completed adversarial action configuration | - | - | - | - | - | - | - | - |

## Chat suite: player-ready gate first

Any visible reasoning, prompt analysis, planning, word/token discussion, or internal assistant discussion is a hard player-ready failure with score 0. Required concepts are scored only on the final player-ready text. Curly apostrophes are normalized to ASCII `'`; normal replies are capped at 950 characters.

| Model | Backend | Ctx | Cache | Mode | Player-ready pass | Meta leaks | Quality mean / min | TTFT p50 / p95 | Gen tok/s p50 | E2E p50 / p95 | Chars p50 / p95 | VRAM headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | ---: |
| qwen3-4b-q4-k-m | cuda | 1024 | disabled | streaming | 0.0% | 7 | 0.00 / 0.00 | 262.95 / 275.59 ms | 44.07 | 985.92 / 1848.69 ms | 118.00 / 302.50 | 4362.00 MiB |

## Cold and warm latency distributions

Cold first-hit requests are intentionally excluded from warm distributions. Warm distributions are p50 / p95 / p99 / max; all case order is randomized each round.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E ms | Warm E2E | TTFT | Prompt tok/s | Gen tok/s |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | disabled | streaming | 652.34 | 985.92 / 1848.69 / 1872.50 / 1878.45 | 262.95 / 275.59 / 275.77 / 275.81 | 1631.93 / 1689.81 / 1705.99 / 1710.04 | 44.07 / 45.01 / 45.02 / 45.02 |

## VRAM and verified backend

VRAM is sampled with nvidia-smi before model load, after load, and during generation. Production selection requires log evidence for the requested backend and full GPU layer offload.

| Suite | Model | Backend | Ctx | Before load used | After load used | Peak during | Min free | Backend evidence |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| chat | qwen3-4b-q4-k-m | cuda | 1024 | 1124.00 | 3828.00 | 3830.00 | 4362.00 | CUDA0; 37/37; full=yes |

## Recommended production configuration

No action configuration qualified: retain the deterministic route until one passes every production action gate.

No chat production winner selected. withheld: meta-reasoning leakage was detected; fix the player-ready gate/model output before selecting a chat winner

## Contract checks

- Selector JSON contract: 7/7 passed.
- Executor-only send_message contract (1-1024 characters): 17/17 passed.
- Normal player-ready chat uses the stricter <=950 character limit before attachment.

Full raw records, normalized player-ready text, timing, VRAM telemetry, and backend evidence: `comparison.json`. Server logs are in `logs/`.
