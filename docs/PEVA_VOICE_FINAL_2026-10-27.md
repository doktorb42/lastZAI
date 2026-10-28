# PEva Voice — final local pilot report, 27 October 2026

## Decision

**No adapter is approved for PEva's normal runtime.**

The final social-repair adapter is the best *technical* experiment of this
small series, but it only tied the clean base on the independent six-case
conversation benchmark. It must remain an evaluation artifact, not a model to
merge, export, select, or connect to Last Z.

The normal PEva runtime was not changed. No GGUF was exported, no adapter was
merged, no game message was sent, and no autonomous action was enabled.

## Scope and fixed base

These are **Voice** experiments, separate from the earlier 7B Agency Pilot
V1. Agency V1 stays immutable and undeployed. Every Voice experiment below
started from the same local clean base, never from an earlier adapter:

- base: `unsloth/Qwen2.5-3B-Instruct-unsloth-bnb-4bit`
- local revision: `21d7790b1332196a5cf65ba72f2b46b659f74ddf`
- QLoRA: rank 16, alpha 32, dropout 0.05, completion-only loss, RTX 3050 8 GB

All generated material was original synthetic authoring. It used no player
messages, game database, nickname/UID, OCR, external model output, network,
ADB, or BlueStacks.

## Results

### Independent, realistic six-case conversation benchmark

| Model | Pass | False completed-action claims | Decision |
| --- | ---: | ---: | --- |
| Clean base | 3/6 | 0/6 | baseline |
| Initial Voice pilot | 2/6 | 0/6 | rejected: repeated/cold and internal-plan output |
| Contextual corrective pilot | 1/6 | 0/6 | rejected: cold replies and repeated continuity failure |
| Social-repair final pilot | 3/6 | 0/6 | tied base; not qualified |

The final pilot fixed the previous empty/internal-plan cases: its coordination
answer was player-facing (`"Sure, I'll check Tyrant with you after reset. Got
your back."`). It still failed two ordinary continuation turns by returning
another status answer after the player had already answered. That is not a
clear social improvement over the base.

### Per-pilot protected splits

Each pair uses that pilot's own family-disjoint 33-row test. These splits are
not interchangeable with one another, so compare only within a row.

| Protected split | Clean base | Adapter | Completed-action claims |
| --- | ---: | ---: | ---: |
| Contextual data (`94e4…5242`) | 25/33 | 33/33 | 0/33 both |
| Social-repair data (`7be4…778c1`) | 26/33 | **29/33** | 0/33 both |

The contextual 33/33 result proves why this technical gate cannot decide
deployment by itself: that adapter scored only 1/6 on the realistic benchmark.
The social-repair adapter's 29/33 is a modest formatting/grounding gain, but
not enough to override the unchanged 3/6 social result.

## Final social-repair experiment

- Adapter: `lastz_model_benchmark/training/outputs/adapters/peva-voice-social-repair-20261027`
- Adapter SHA-256: `50b89ab591ed1fcda24a800c9172b446c58b078d6c8760182d396d85dc47d46a`
- Data: 330 unique prompts; 264 train / 33 validation / 33 protected test
- Source SHA-256: `2e1c39db496a761aa748525b3512918e05ae3382777b0e6a6b90afb20ee67642`
- Train / validation / protected-test SHA-256:
  `194ad0122dd9ba0b22526d6906f1eb91c4f74322619368d11405031ff0dab2b5`,
  `563a11c100400b72e1795b101f38c8dca7dbed145c905848ee97af3a8ea501eb`,
  `7be41911e947488a181dedc34fb375a252180f1beae3cb3ad2c40b5ccc5778c1`
- One epoch, LR `2e-5` (the only deliberate hyperparameter change). This was
  reduced from `5e-5` after the prior pilot overwrote too much of the base
  conversational behaviour.
- Runtime: 261 seconds; peak reserved GPU memory: 4.22 GiB.

Its source explicitly excluded observed bad templates such as `"I am not
interested"` and `"I will check"`, included friendly greetings, disagreement,
clarification, scheduling, game discussion, natural short reactions, and
bounded uncertainty. It passed validation, deduplication, frozen-fixture
leakage scanning (31 fixtures), and player/thread/variant-disjoint splitting.

## Preserved evidence

- Initial rejected Voice adapter:
  `lastz_model_benchmark/training/outputs/adapters/peva-voice-pilot-20261027`
- Contextual rejected adapter:
  `lastz_model_benchmark/training/outputs/adapters/peva-voice-contextual-20261027`
- Final social-repair adapter (evaluation only):
  `lastz_model_benchmark/training/outputs/adapters/peva-voice-social-repair-20261027`
- Training reports: `lastz_model_benchmark/training/outputs/reports/`
- Saved candidate outputs: `lastz_model_benchmark/training/outputs/evaluations/`
- Final data readiness:
  `lastz_model_benchmark/training/data/reports/peva_voice_social_repair_readiness_report.json`
- The one incomplete social-repair source was preserved, not deleted, in:
  `archive/training_rejected/peva_voice_social_repair_missing_long_20261027/`

## Verification and resource state

- Full project tests: 143 passed.
- Main integration/security tests: 247 passed.
- Training pipeline tests: 22 passed.
- Benchmark contract/quality tests: 7 passed.
- GPU after the final evaluation: 765 MiB / 8192 MiB in use, 10% utilisation;
  this is normal desktop/system use, not a PEva Python process.
- WSL process check: no `train_chat_lora.py` or `evaluate_chat_adapter.py`
  process remained.

## Safe next step

Do not train another synthetic pilot automatically. The evidence says the next
useful input is a compact, user-reviewed set of real-but-consented conversation
contexts with explicit desired replies, especially multi-turn acknowledgement
and scheduling examples. First run the base and any future adapter in
preview-only manual chat tests; only then consider a new clean-base experiment.
