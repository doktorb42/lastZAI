# PEva V2 conversation and player-memory foundation

## Status and non-goals

This package is the canonical PEva V2 conversation/memory foundation. It
implements versioned model contracts, Python-owned validation, durable storage,
and immutable request snapshots before any Chat V1 or Memory V1 training.

It intentionally does **not**:

- modify, retrain, convert, or benchmark the frozen Action V1 model;
- create Chat V1 or Memory V1 training data;
- alter the active root runtime or make it depend on this package;
- interact with BlueStacks, ADB, screenshots, or game controls;
- auto-send a chat message.

This separation is important: a correct training format cannot be inferred from
mutable live state after the fact. PEva V2 stores the exact prompt/snapshot used
at the time of a future request, then exports only human-reviewed capture
records for a later data-governance step.

## Canonical architecture

```text
Action V1 (frozen, separate)
  {"action":"reply_private"}
             |
             v
future root capture adapter supplies verified observation + routing metadata
             |
             v
V2 routing/binding store (Python only)
  external room_id + channel + counterpart game UID + optional PEva game UID
  verification state + stable source-message key
             |
             v
V2 conversation repository
  identity -> dedup -> lifecycle state -> immutable alias-only snapshot
             |
      +------+--------------------------+
      |                                 |
      v                                 v
<TASK_CHAT>                       <TASK_MEMORY>
plain player-ready text            strict peva-memory-update-v2 JSON
      |                                 |
hard output gate + review          validator + explicit parameterized apply
      |                                 |
      +---------- future reviewed delivery bridge ----------+
                                                        |
                                                 confirmation only
```

The model never writes SQL or opens a database. It cannot nominate a table,
database path, player UUID, external room, game UID, or arbitrary executor
action. Model output is data parsed and constrained by Python before a fixed,
parameterized storage operation can happen.

## Identity and routing boundary

There are two deliberately different ID domains.

### Runtime/storage domain

The repository owns stable internal identity and routing/binding information:

- a verified counterpart game-player UID or operator-confirmed binding;
- external Last Z `room_id` and `channel`;
- an optional PEva game UID where a delivery adapter needs it;
- verification status and confidence;
- source-message/delivery keys used for idempotency;
- internal database IDs and revision/audit references.

These values decide whether the observation is safe to accept and where a
future delivery adapter can route an already approved reply. They are never a
part of a model task payload.

### Model domain

Each immutable context snapshot uses only task-local aliases:

| Entity | Alias form |
|---|---|
| player | `p0` |
| conversation | `c0` |
| message | `m0`, `m1`, ... |
| memory | `mem0`, `mem1`, ... |
| thread | `thread0`, ... |
| promise | `promise0`, ... |

Alias assignment is deterministic within a snapshot. It is not a global public
identifier. The storage layer preserves the internal alias-to-record mapping for
validation, while the serialized model input contains no raw routing or SQLite
identifier.

## V2 contract versions

```text
context_contract_version = "peva-conversation-context-v2"
chat_prompt_version      = "peva-chat-input-v2"
memory_prompt_version    = "peva-memory-input-v2"
memory_update_version    = "peva-memory-update-v2"
```

The literal task tags remain `<TASK_CHAT>` and `<TASK_MEMORY>`. `<TASK_ACTION>`
is logical routing terminology only: it does not alter frozen Action V1's trained
prompt surface.

## Canonical context: `peva-conversation-context-v2`

Every chat and memory request uses the same serialized `context` object:

```json
{
  "as_of": "2026-10-14T12:00:00Z",
  "conversation_ref": "c0",
  "channel": "private",
  "player": {
    "player_ref": "p0",
    "nickname": "Nova",
    "language": "en",
    "alliance": "Night Owls",
    "alliance_role": null,
    "relationship_style": "friendly_casual",
    "identity_status": "verified"
  },
  "recent_messages": [
    {
      "message_ref": "m1",
      "direction": "outgoing",
      "text": "yeah I should be around after reset",
      "observed_at": "2026-10-14T11:57:00Z"
    }
  ],
  "long_term_memory": [
    {
      "memory_ref": "mem0",
      "kind": "fact",
      "key": "schedule.work_shift",
      "value": "Often works late shifts and can miss Tyrant.",
      "confidence": 0.92
    }
  ],
  "temporary_memory": [],
  "open_threads": [],
  "pending_promises": [],
  "conversation_summary": null,
  "incoming_message": {
    "message_ref": "m0",
    "direction": "incoming",
    "text": "can you join after reset?",
    "observed_at": "2026-10-14T12:00:00Z"
  }
}
```

The serialized snapshot is retained verbatim with its hash. It is not
reconstructed from the current player profile, current memory rows, or current
conversation history. If compaction occurs, that fact is audit metadata and the
record is not eligible for later training-candidate export.

## `<TASK_CHAT>` contract

The exact user payload follows this form; the system message is separately
persisted with it:

```json
{
  "task": "<TASK_CHAT>",
  "prompt_version": "peva-chat-input-v2",
  "context_contract_version": "peva-conversation-context-v2",
  "context": {
    "as_of": "2026-10-14T12:00:00Z",
    "conversation_ref": "c0",
    "channel": "private",
    "player": {
      "player_ref": "p0",
      "nickname": "Nova",
      "language": "en",
      "alliance": "Night Owls",
      "alliance_role": null,
      "relationship_style": "friendly_casual",
      "identity_status": "verified"
    },
    "recent_messages": [],
    "long_term_memory": [],
    "temporary_memory": [],
    "open_threads": [],
    "pending_promises": [],
    "conversation_summary": null,
    "incoming_message": {
      "message_ref": "m0",
      "direction": "incoming",
      "text": "can you join after reset?",
      "observed_at": "2026-10-14T12:00:00Z"
    }
  },
  "output_contract": {
    "kind": "player_ready_english_text",
    "persona": "natural_casual_last_z_player",
    "max_characters": 950,
    "straight_ascii_apostrophes_only": true,
    "no_visible_reasoning": true
  }
}
```

Output is only final player-ready text, for example `yeah bro, I should be
around after reset`. There is no JSON wrapper. The hard gate rejects visible
reasoning (`Okay, the user wants...`), prompt discussion, planning, Markdown,
curly apostrophes unless normalized before approval, and replies over 950
characters.

## `<TASK_MEMORY>` contract

The context is byte-for-byte the same stored snapshot that was used for chat.
`allowed_refs` are aliases from that snapshot, never raw database IDs.

```json
{
  "task": "<TASK_MEMORY>",
  "prompt_version": "peva-memory-input-v2",
  "context_contract_version": "peva-conversation-context-v2",
  "context": {
    "as_of": "2026-10-14T12:00:00Z",
    "conversation_ref": "c0",
    "channel": "private",
    "player": {
      "player_ref": "p0",
      "nickname": "Nova",
      "language": "en",
      "alliance": "Night Owls",
      "alliance_role": null,
      "relationship_style": "friendly_casual",
      "identity_status": "verified"
    },
    "recent_messages": [],
    "long_term_memory": [],
    "temporary_memory": [],
    "open_threads": [],
    "pending_promises": [],
    "conversation_summary": null,
    "incoming_message": {
      "message_ref": "m0",
      "direction": "incoming",
      "text": "I work late shifts so I usually miss Tyrant",
      "observed_at": "2026-10-14T12:00:00Z"
    }
  },
  "approved_reply": "ah got you bro, we'll try to ping you earlier when we can",
  "allowed_refs": {
    "message_refs": ["m0"],
    "memory_refs": [],
    "thread_refs": [],
    "promise_refs": []
  },
  "output_contract": {
    "schema_version": "peva-memory-update-v2",
    "strict_json": true,
    "max_operations": 8,
    "allowed_operations": [
      "noop",
      "upsert_memory",
      "expire_memory",
      "set_profile_field",
      "open_thread",
      "close_thread",
      "open_promise",
      "close_promise"
    ]
  }
}
```

A valid output is:

```json
{
  "schema_version": "peva-memory-update-v2",
  "operations": [
    {
      "op": "upsert_memory",
      "kind": "fact",
      "key": "schedule.work_shift",
      "value": "Often works late shifts and may miss Tyrant.",
      "scope": "long_term",
      "confidence": 0.92,
      "expires_at": null,
      "evidence_message_refs": ["m0"]
    }
  ]
}
```

The accepted operations are `noop`, `upsert_memory`, `expire_memory`,
`set_profile_field`, `open_thread`, `close_thread`, `open_promise`, and
`close_promise`. Allowed memory kinds are `fact`, `preference`, `relationship`,
`game_context`, and `communication_style`. Temporary memories require a future
`expires_at`. There is no model-written importance and no summary replacement;
summary generation belongs to a future `TASK_SUMMARY` contract.

## Storage and lifecycle guarantees

The canonical storage model tracks:

- stable player identity, verified aliases, external identity bindings, and
  channel/room routing separately from model context;
- immutable inbound/outbound message records and idempotent source keys;
- long-term and temporary memory with confidence, expiry, evidence, status, and
  revision history;
- profile-field confidence, open threads, pending promises, and summaries;
- immutable context snapshots, exact prompt messages, model outputs, review
  decisions, validation outcomes, and delivery acknowledgements.

The cursor/lifecycle terminology is deliberately distinct: **ingested** means a
message is durable; **processed** means its workflow reached the defined state;
draft generation, human approval, memory application, and confirmed delivery
are individual state transitions. Ingesting a message alone must not advance it
to fully processed.

## Export readiness, not training

An explicit exporter may write approved-capture **review records** only when all
of these are true:

1. the player identity and routing binding were verified;
2. the original snapshot is untruncated and V2-valid;
3. the reply was human-approved and external delivery was confirmed;
4. the associated memory proposal was Python-validated and explicitly applied.

The review record preserves the original V2 context and exact prompt messages;
it does not rebuild them from live tables. It is not a dataset. Before any
future training there must be a separate redaction, quality review,
deduplication, scenario-family split, and player/conversation-disjoint holdout
step.

## Deliberately unintegrated root runtime

The active root files are unchanged by this phase:

- `main.py`, `agent.py`, `conversation_store.py`, `memory_engine.py`;
- `chat_capture.py`, `action_ex.py`, `adb_controll.py`.

The future integration should retain root capture/executor adapters while
replacing parallel conversation/memory writes with one V2 service call. It must
map Action V1's abstract `reply_private` result to a reviewed V2 handoff and
only use raw `room_id`/UID values outside model prompts. Until reliable
counterpart verification exists, no root runtime integration is authorized.

## Verification scope

Unit tests cover V2 routing/identity gates, deterministic alias snapshots,
absence of raw routing-ID leakage, independent lifecycle states, duplicate
ingest, revision/evidence/expiry validation, injection and unknown-field
rejection, player-ready text checks, shared chat/memory context, and
review-record export integrity. They use temporary SQLite databases and never
contact a model endpoint or a game client.
