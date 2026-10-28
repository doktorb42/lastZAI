# Last Z action-selection benchmark

This benchmark is intentionally narrow.  `screenDetector.py` already reduces
the game screen to a compact state; an LLM therefore only needs to select one
of a few actions and produce valid JSON.  Evaluating generic chat quality or
vision ability would give the wrong answer for this project.

`cases/decision_cases.jsonl` captures the current policy in `agent.py`,
including its `pending_private_message` memory.  Every case supplies only
actions that are valid for its screen.  A candidate must pass all of these:

- valid JSON and no extra keys;
- action and arguments from the supplied allowlist;
- exact agreement with the current safe policy;
- acceptable p50/p95 warm-response latency while loaded on the GPU.

The runner uses the OpenAI-compatible local API of `llama-server` and a JSON
schema.  It does not import the application, connect to ADB, inspect the game,
or execute any action.

## Run

```powershell
# From the project root, after the local download script has finished
python .\lastz_model_benchmark\benchmarks\run_benchmark.py
```

Useful variants:

```powershell
# More stable latency estimate (five requests per case)
python .\lastz_model_benchmark\benchmarks\run_benchmark.py --iterations 5

# Test one manually supplied GGUF
python .\lastz_model_benchmark\benchmarks\run_benchmark.py --model D:\models\candidate.gguf

# Inspect model discovery and the command configuration without loading a model
python .\lastz_model_benchmark\benchmarks\run_benchmark.py --dry-run
```

Reports are written to `reports/` as JSON (full raw samples) and Markdown
(short decision table).  The runner picks the fastest candidate which has a
100% legal-action rate and the highest exact-match rate; if no candidate clears
the safety threshold, it prioritizes exact-match rate over speed.

The default configuration is appropriate for this machine: RTX 3050 8 GB,
`-ngl 99`, 2048-token context, 16 CPU threads, and at most 24 output tokens.
The Qwen3 candidate is asked not to reason, because hidden reasoning adds
latency without helping this finite action-selection task.

## Extended performance and reliability benchmark

`run_performance_benchmark.py` is an addition, not a replacement for the
compact runner above. It keeps the literal legacy rows in
`decision_cases.jsonl`, then separates new fixtures into:

- `production_action_cases.jsonl`: only real `action_ex.py` executor
  capabilities for that screen. Qualification is 100% valid JSON,
  schema-valid, legal, and exact decisions.
- `adversarial_action_cases.jsonl`: tempting, unavailable, injected, or
  screen-invalid controls. Its robustness score is reported separately and
  never lowers the production qualification rate.
- `chat_cases_realistic.jsonl`: realistic very-short, short, medium, and
  occasional longer English private replies (5-120 words, max 950 characters).
- `chat_v1_quality_cases.jsonl`: Chat V1 social/private-conversation fixtures,
  including direct greetings, expected replies, and continuity through turns
  2-5. Each has a validated canonical `peva-conversation-context-v2` and is
  rendered through the exact `<TASK_CHAT>` / `peva-chat-input-v2` renderer used
  by runtime and Chat V1 training. One-word natural acknowledgements such as
  `yo` and `nice` are allowed only in these explicit social fixtures; this
  does not loosen the normal coordination-length fixtures.
- `chat_v1_quality_gate_vectors.jsonl`: deterministic no-model regression
  vectors for generic-assistant tone, support-agent wording, AI/program
  disclaimers, persona denials, conversation resets, excessive formality,
  expected-empty replies, and straight-apostrophe normalization.

The action selector never generates a game message. It can emit virtual
`{"action":"reply_private"}` only as a planner handoff; a separate chat step
normalizes and validates text before production code may construct the raw
executor payload `{"action":"send_message","text":"..."}`. The latter has
its own 1-1024-character contract; normal player replies use the stricter 950
character cap. Curly apostrophes are normalized to ASCII `'` and recorded.

For legacy performance fixtures, `/no_think` is appended to the final user
prompt; canonical Chat V1 fixtures instead retain the V2 renderer unchanged,
whose system message already contains `/no_think`. `--reasoning-budget 0` is
used in both paths. Visible reasoning, prompt analysis, planning, word/token
discussion, or internal-assistant text is a hard player-ready failure with
score 0. A chat winner is withheld whenever such leakage occurs.
Chat V1 additionally treats customer-support/ChatGPT language as a hard
quality failure even when it is grammatical. The report records category
counts for `generic_assistant_tone`, `support_agent_language`,
`self_identifies_as_program`, `self_identifies_as_ai`,
`denies_normal_persona`, `unnatural_followup`, `conversation_reset`,
`excessive_formality`, and `empty_reply_when_reply_expected`. It does not
require slang, `bro`, `xD`, or emojis: broad social-intent patterns and
human-review examples preserve relationship- and context-dependent style.
The assistant-like score is a shared **risk** score with dataset validation:
`0` is clean and any positive value fails the hard player-ready tone gate.

Run the Chat V1 fixture contract without downloading/loading a model or
starting `llama-server`:

```powershell
python .\lastz_model_benchmark\benchmarks\run_performance_benchmark.py `
  --validate-chat-v1-quality
```

It never opens BlueStacks, invokes ADB, takes game screenshots, or performs a
real action. Every request goes only to a temporary local `llama-server`.

```powershell
# Broader local matrix (CUDA/Vulkan when installed, all configured contexts)
python .\lastz_model_benchmark\benchmarks\run_performance_benchmark.py

# Focused production comparison used on this RTX 3050 machine: CUDA only,
# Qwen2.5 3B versus Qwen3 4B, ctx 1024, cache disabled/enabled, streaming chat.
python .\lastz_model_benchmark\benchmarks\run_performance_benchmark.py `
  --suites production_action,adversarial_action,chat `
  --backends cuda `
  --server-cuda .\lastz_model_benchmark\runtime\llama-cpp-cuda-b11430\llama-server.exe `
  --model .\lastz_model_benchmark\models\weights\qwen2.5-3b-instruct-q4_k_m.gguf `
  --model .\lastz_model_benchmark\models\weights\Qwen3-4B-Q4_K_M.gguf `
  --action-contexts 1024 --chat-contexts 1024 `
  --cache-modes disabled,enabled --chat-modes streaming `
  --gpu-layers 99 --max-action-tokens 24 `
  --action-rounds 3 --chat-rounds 3 --warmup 2 --speculative off
```

When a verified project-local CUDA runtime exists under
`lastz_model_benchmark/runtime/llama-cpp-cuda-*/`, the runner discovers it
automatically and compares `CUDA0` with the global Vulkan server. It does not
modify PATH or replace the system llama.cpp installation.

Each report lives in one timestamped `reports/performance_*/` folder together
with that run's server logs. It includes cold versus warm samples, randomized
case order, TTFT/TTFB/end-to-end p50/p95/p99/max distributions, prompt and
generation speed, VRAM before/after/during generation, actual backend evidence
parsed from the server log, and full-GPU-offload verification. CUDA is marked
unavailable when the supplied server binary does not expose a CUDA device; it
is never inferred merely because an NVIDIA GPU exists.

The Markdown report separates production action exact/legal/schema rates from
adversarial robustness. Its **Chat V1 quality report** shows player-ready pass
rate, meta-reasoning leaks, assistant-like failure counts, expected-empty
reply failures, continuity failures, per-category counters, character counts,
latency, throughput, and VRAM. Chat ranking is quality-first, then streaming
TTFT and generation speed. Optional `ngram-cache`
speculation is measured only when the installed llama.cpp build advertises
support; no unverified draft-model pairing is assumed.
