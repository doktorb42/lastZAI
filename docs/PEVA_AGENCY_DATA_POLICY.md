# PEva Agency data inclusion policy

This policy keeps historical evidence available while making positive training inclusion deliberately strict.

## Source separation

- `data/training/agency/real/` is a human review queue. Every row is `source: real_unreviewed`, `synthetic: false`, and starts with `eligible_for_positive_training: false`.
- `data/training/agency/synthetic/` contains only explicitly labelled curated seed data. It is never counted as real conversation evidence.
- Historical SQLite data remains in the runtime stores. Cleanup classifications live in `data/cleanup/peva_data_cleanup_manifest.json`; no new SQLite status enum is introduced.

## Positive real-data requirements

`agency_dataset_policy.py` is the central rule implementation. A real private-chat record may enter a positive dataset only when all of the following hold:

- it is a direct numeric `custom_<uidA>_<uidB>_vN` room, not a public/group room;
- it is not a test-marked record, a dummy test UID, or a record externally classified `EXCLUDE_POSITIVE`;
- it has an explicit `GOOD` rating or an `EDITED` rating with a safe correction;
- it has no unsafe unconfirmed action claim, technical output, benchmark leakage, automatic fallback, or duplicate fingerprint.

`rating = null` means `UNREVIEWED`, never `GOOD`.

## Conservative cleanup behavior

Cleanup never deletes conversation, feedback, memory-review, or humanization records. It may only use existing schema semantics:

- the known false runtime memory is set to `active = 0`;
- known test memory-review candidates are set to existing status `REJECTED`;
- public/group, test, unreviewed, unsafe, and automatic-profile material is classified externally and excluded from positive training.

The strict production QLoRA gate is independent from the Agency seed data. Synthetic examples cannot satisfy the production real-data requirement.
