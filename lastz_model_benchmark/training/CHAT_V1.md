# PEva Chat V1 preparation pipeline

Chat V1 is isolated under `lastz_model_benchmark/training/`. It does not
modify the frozen Action V1 adapter, root runtime, conversation SQLite database,
or any game client. It also does not train Memory V1.

## Canonical source record

The review format is `peva-chat-v1-source-record-v1`. Its `task_snapshot`
contains the exact immutable V2 `<TASK_CHAT>` input and exact two-message
renderer output from `conversation_system`; it is never rebuilt into a
training-only prompt. The source record separately stores:

- `model_draft` — optional original draft, preserved during edits;
- `approved_reply` — the only possible training target;
- `approval` — explicit human state, reviewer, timestamp and reason;
- `grouping.player_group` / `grouping.thread_group` — opaque `pgrp_*` / `tgrp_*`
  aliases used solely for splitting;
- reviewed `category`, provenance, source type and optional valid-variant group.
- reviewed `length_band`: `micro_social` (1–4 words) for natural acknowledgements,
  then `very_short` (5–20), `short` (15–35), `medium` (35–70), or
  `occasional_long` (70–120).

No group, reviewer, approval, provenance, route, UID, room, SQLite identifier,
or draft is placed in `prompt` or `completion`. The eventual completion is one
assistant message containing only the approved player-ready reply.

`generate_teacher_chat_data.py` is intentionally deprecated and blocked: its
historical prompt format was not the canonical V2 contract, so it cannot become
an alternate Chat V1 data path.

## Workflow

Import a fully approved V2 export only after assigning opaque grouping aliases,
a category, and a truthful reply-length band. The exporter preserves the original
model draft separately from the approved/edited target. The importer refuses to
infer a group from hidden runtime IDs.

```powershell
py -3.12 .\lastz_model_benchmark\training\scripts\prepare_chat_v1_dataset.py `
  --input .\lastz_model_benchmark\conversation_system\data\exports\peva_v2_approved_chat_review_records_YYYY.jsonl `
  --format v2-review-export --player-group pgrp_nova --thread-group tgrp_nova_private `
  --category casual_greeting --length-band micro_social
```

A mixed multi-player/thread V2 export must use a row-to-alias manifest; one
global alias is deliberately refused because it would destroy split isolation.
The manifest contains only export hashes and opaque aliases, never Last Z IDs:

```json
{
  "schema_version": "peva-chat-v1-group-manifest-v1",
  "records": {
    "<64-character-review-record-hash>": {
      "player_group": "pgrp_nova",
      "thread_group": "tgrp_nova_private",
      "category": "casual_greeting",
      "length_band": "micro_social"
    }
  }
}
```

Pass it with `--group-manifest .\path\chat_v1_groups.json`. The queue is
replacement-safe: importing into an existing queue requires explicit `--append`
and rejects duplicate source record IDs.

Review the queue. An edit is saved as `approved_reply`, while its original
`model_draft` remains unchanged. A category is mandatory for approval.

```powershell
py -3.12 .\lastz_model_benchmark\training\scripts\review_chat_v1.py `
  --input .\lastz_model_benchmark\training\data\raw\chat_v1_source_queue.jsonl `
  --reviewer operator_1
```

Run the immutable gates in order:

```powershell
py -3.12 .\lastz_model_benchmark\training\scripts\validate_chat_v1_dataset.py --fail-on-reject
py -3.12 .\lastz_model_benchmark\training\scripts\check_chat_v1_leakage.py
py -3.12 .\lastz_model_benchmark\training\scripts\split_chat_v1_dataset.py
py -3.12 .\lastz_model_benchmark\training\scripts\chat_v1_readiness.py
```

The readiness script prints `CHAT V1 TRAINING READY` only when all reports are
hash-linked and passed, Action V1's frozen protected-production comparison is
qualified, all train/validation/test splits are non-empty and group-disjoint,
zero quality/leakage counters remain, every requested coverage category is
present, every required length band is represented, and at least 300 approved
valid records exist. The 300-row threshold is a minimum; the initial reviewed
pilot target is 500.

## Split algorithm

`split_chat_v1_dataset.py` creates connected components over player group,
thread group, and explicit variant group. A component is assigned as one unit
to train, validation, or protected test using a deterministic seeded,
ratio-balanced procedure. No player, private conversation/thread, or explicit
alternative variant can cross a split boundary.

## Pilot QLoRA starting point

For the expected 300–500 reviewed examples, `configs/chat_v1.yaml` recommends
Qwen2.5-3B-Instruct through Unsloth 4-bit QLoRA: rank 16 / alpha 32 / dropout
0.05, batch 1, gradient accumulation 16, LR `5e-5`, warmup `0.05`, three
epochs, `adamw_8bit`, completion-only loss, and a 2048-token context. This is
deliberately lower-LR and regularized compared with Action V1. It must be
reviewed through an explicit CUDA run; there is no CPU fallback.

The trainer performs a tokenizer preflight after loading the actual tokenizer
and refuses every prompt+completion that would exceed 2048 tokens. It will not
silently truncate the canonical V2 prompt. It also rejects a non-empty adapter
output directory and does not open, tokenize, or give the protected test split
to the trainer process.

The eventual full training command is intentionally explicit and still gated:

```powershell
wsl.exe --distribution Ubuntu -- /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/.venv-wsl-cu126/bin/python /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/scripts/train_chat_lora.py --run-full
```

Before that command, run its safe gate-only form with the same required flag:

```powershell
wsl.exe --distribution Ubuntu -- /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/.venv-wsl-cu126/bin/python /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/scripts/train_chat_lora.py --run-full --dry-run
```

Neither command has been run by the preparation pipeline.
