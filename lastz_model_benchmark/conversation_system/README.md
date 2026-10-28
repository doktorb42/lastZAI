# PEva V2 Player Memory + Conversation System

This directory is the isolated, canonical foundation for PEva's future private
conversation and player-memory system. It is deliberately separate from the
active Last Z runtime while the capture and delivery boundary is being made
reliable.

It does **not** train a model, create a Chat V1 or Memory V1 dataset, alter the
frozen Action V1 model, start BlueStacks, call ADB, or send a game action.

## Canonical V2 boundary

```text
Frozen Action V1 selector                 existing root runtime (unchanged)
  {"action":"reply_private"}                         |
                                                        v
Future verified capture adapter -> external routing/identity check
                                     (Python + SQLite only)
                                                        |
                                                        v
                         immutable peva-conversation-context-v2 snapshot
                                                        |
                    +-----------------------------------+------------------+
                    |                                                      |
                    v                                                      v
          <TASK_CHAT> local model                                 <TASK_MEMORY> local model
          player-ready text only                                  strict JSON proposal only
                    |                                                      |
          hard output gate -> human review                  Python validation -> explicit apply
                    |                                                      |
                    +-----------------------> future reviewed delivery bridge
                                                        |
                                                        v
                                         delivery confirmation recorded
```

The model has no database connection and never receives SQL, file paths, raw
SQLite UUIDs, Last Z player UIDs, external room IDs, or routing identifiers.
Only Python-owned, parameterized SQLite code can change persisted state. A
future bridge can attach an approved chat reply to an executor handoff only
after identity/routing validation; that bridge is not implemented here.

## Versioned contracts

| Contract | Stable version | Purpose |
|---|---|---|
| Canonical conversation context | `peva-conversation-context-v2` | Immutable model-facing snapshot for runtime, benchmarks, validation, and future export. |
| Chat input | `peva-chat-input-v2` | `<TASK_CHAT>` prompt payload; output is player-ready text, not JSON. |
| Memory input | `peva-memory-input-v2` | `<TASK_MEMORY>` prompt payload; output is strict JSON. |
| Memory update | `peva-memory-update-v2` | Python-validated update proposal. |

`<TASK_ACTION>` remains an independent logical label for frozen Action V1. Its
prompt, model, benchmark baseline, and action contract are not changed by this
system.

### Model-facing aliases versus real routing IDs

Every V2 snapshot uses fresh, task-local aliases only:

- player: `p0`
- conversation: `c0`
- messages: `m0`, `m1`, ...
- memories: `mem0`, `mem1`, ...
- threads: `thread0`, ...
- promises: `promise0`, ...

The storage/routing layer separately owns the verified counterpart binding,
external Last Z `room_id`, channel, optional PEva game UID, and verification
state. Those values are used to decide whether an observation can enter the
system and, much later, where a reviewed handoff may be delivered. They are
never serialized into the `context` supplied to either model task.

## Task contracts

### `<TASK_CHAT>`

The input has exactly `task`, `prompt_version`, `context_contract_version`,
`context`, and `output_contract`. `context` contains:

- `as_of`, `conversation_ref`, and `channel`;
- `player` (nickname and known profile fields, using `player_ref: "p0"`);
- `recent_messages`, active long-term and temporary memories;
- open threads, pending promises, and one compact conversation summary;
- the `incoming_message` that is being answered.

The answer is only natural player-ready English text. It must pass the hard
reply gate: no visible reasoning or prompt analysis, no Markdown/JSON wrapper,
at most 950 characters, and straight ASCII apostrophes (`'`).

### `<TASK_MEMORY>`

The input includes the *same serialized context snapshot* as `<TASK_CHAT>`, an
already approved reply, `allowed_refs`, and an output contract. The model may
return only a `peva-memory-update-v2` object. Its allowed operations are:

- `noop`
- `upsert_memory`, `expire_memory`
- `set_profile_field`
- `open_thread`, `close_thread`
- `open_promise`, `close_promise`

There is no `replace_summary` operation; summary generation is reserved for a
future `TASK_SUMMARY`. Memory values have `kind`, `key`, `value`, `scope`,
`confidence`, `expires_at`, and `evidence_message_refs`. The model never sets
importance. Python owns retrieval/ranking policy. Temporary memory must have a
future expiry and all evidence/reference aliases must be present in the
snapshot's `allowed_refs`.

## Lifecycle and immutable audit trail

An incoming message is not considered processed merely because it was seen.
The storage lifecycle distinguishes at least:

1. verified and ingested;
2. immutable snapshot created;
3. chat draft generated or rejected;
4. reply approved or edited by a human;
5. memory proposal validated and explicitly applied;
6. reviewed handoff made available;
7. external delivery independently confirmed.

Duplicate observation keys are idempotent. The context and exact system/user
messages used for every model request are persisted at request time. Later
memory changes cannot reconstruct or replace that snapshot.

The exporter is intentionally an explicit, review-only operation. It can write
immutable approved-capture records for a later redaction, deduplication,
scenario-family split, and player/conversation-disjoint holdout review. It does
not populate a training-data path, invoke a trainer, or turn captures into a
training dataset.

## First-rollout safety

Only `person_index=0` is currently accepted, but list position is never treated
as identity. The future capture adapter must provide a stable source-message
key, verified external counterpart binding, visible nickname, routing room, and
confidence. A mismatch is quarantined before a model prompt, memory mutation,
or handoff can occur.

For isolated operator testing, `enroll-player` accepts `--external-room-id`,
`--counterpart-game-player-id`, and optional `--peva-game-player-id`; the
storage-only `bind-route` command can revise an existing verified route. The
`capture` command accepts matching route fields. These are ingress and delivery
metadata only: neither command can send a game action, and none of those values
appears in a TASK_CHAT or TASK_MEMORY payload.

The root runtime files remain intentionally unintegrated:

- `main.py`, `agent.py`, `conversation_store.py`, `memory_engine.py`
- `chat_capture.py`, `action_ex.py`, `adb_controll.py`

They will require a separate, explicit integration change after reliable
counterpart/room verification exists. This package does not import them.

## Safe local commands

Run from `D:\projekty\lastZAI`:

```powershell
py -3.12 -m lastz_model_benchmark.conversation_system.lastz_conversation --help
py -3.12 -m unittest discover -s .\lastz_model_benchmark\conversation_system\tests -v
```

`init` creates only the isolated SQLite schema. Any `export-approved` command
is an explicit review-record export; it is not a training command.

See [DESIGN.md](DESIGN.md) for full V2 payload examples, storage separation,
and the phased migration plan.
