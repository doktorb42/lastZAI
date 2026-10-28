# PEva Voice pilot — 27 October 2026

> This document records the first rejected pilot and its then-current plan.
> The contextual and social-repair follow-ups have now completed. Read
> `docs/PEVA_VOICE_FINAL_2026-10-27.md` for the final comparison and the
> current no-deployment decision.

## Status

**Rejected as a runtime candidate.** The adapter is preserved as evidence, but
was not merged, exported to GGUF, selected in runtime configuration, loaded by
PEva's normal launcher, or connected to game actions.

## What ran

- Base: local clean `unsloth/Qwen2.5-3B-Instruct-unsloth-bnb-4bit`, revision
  `21d7790b1332196a5cf65ba72f2b46b659f74ddf`.
- Adapter: `lastz_model_benchmark/training/outputs/adapters/peva-voice-pilot-20261027`
  (`SHA-256 97519e7d84d8cc8dd8b1564e5c426cedfeadd5062343330b2e7ca367bd52e53c`).
- QLoRA: rank 16, alpha 32, dropout 0.05, LR `5e-5`, three epochs,
  completion-only loss, RTX 3050 8 GB.  Runtime: 875 s; peak reserved GPU
  memory: 4.85 GiB.
- Data: 308 original, synthetic PEva examples.  No player chat, game database,
  name/UID, image, public roleplay text, or third-party dialogue was included.
  They passed duplicate, prompt-contract, path/identifier-leak, and frozen
  benchmark-overlap scans.  Split: 252 train / 28 validation / 28 held out.

The word *approved* in the source-record technical field means authorised for
this controlled pilot. It does **not** claim that every example was reviewed
by a human social evaluator.

## Measured result

| Check | Clean base | Pilot adapter | Outcome |
| --- | ---: | ---: | --- |
| Held-out technical quality screen | 13/28 | 28/28 | format/style filter improved |
| Potential false claim of game action | 0/28 | 0/28 | preserved |
| Empty required reply | 0/28 | 0/28 | preserved |
| Assistant-support wording | 0/28 | 0/28 | preserved |

The result is **not** a pass. Manual inspection of the saved, greedy outputs
found collapse to several repeated responses, including an unsupported
"I meant the one with the red hat." and unusually cold repetitions such as
"I do not care about your opinion either.". The group-disjoint split guarded
against train/test family leakage, but its four held-out situations still had
repeated incoming wording across alternatives, so it was too weak to expose
this failure by itself.

The quality gate now additionally rejects prompt-wrapper text such as
"Sure, here's my reply:". It has a regression test. The two saved screening
reports predate that extra rule and are retained unchanged as audit evidence.

## Safety and privacy boundary

- The normal PEva runtime remains preview-only (`Action(NONE)`); no test sent a
  game message or launched an autonomous action.
- Local-model configuration now rejects non-loopback LLM endpoints before any
  request is made. This protects PEva's model path from a remote endpoint
  accidentally appearing in settings or environment variables.
- Training and evaluation used the existing local checkpoint and local files;
  no model download was used. This does not make claims about Last Z's own
  normal network traffic, which is outside PEva's local model code.

## Verification

- `testy`: 143 passed.
- main `tests`: 247 passed.
- training tests: 18 passed.
- benchmark quality tests: 7 passed.

## Next safe experiment

The next pilot must again start from the clean base, never this adapter. Its
source set needs one grounded target per genuinely distinct conversation
context rather than several alternative targets for one identical prompt. It
must pass both the protected split and the six-case, multi-turn social quality
benchmark before manual chat testing. A first attempt to start the latter was
not run: WSL could not create its VM while BlueStacks was using about 3.6 GiB
of working memory. BlueStacks was intentionally not closed or changed.

## Corrective corpus prepared (not trained yet)

`peva_voice_contextual_source.jsonl` contains 330 original synthetic examples
with exactly one target for each of 330 distinct canonical TASK_CHAT inputs.
It covers eleven conversation families equally (30 each), includes every
configured reply-length band, has no explicit alternative-target group, and
uses no real capture or external dialogue. Its current hashes are:

- source: `9a6cfd4a1dd6c757f88583efedca281529bb18a9fe9206e3a7d7dbb7d2dc7697`;
- train: `9b0a32ecc8533caf382e001aa839f4a0960c3d05732c6a6f7b727c864019c769`;
- validation: `fdb34c4e2eb825fdbfa6376863a0b4569bec54f2a0df3529c43ad2e54883f79b`;
- protected test: `94e4e3aea9a370c7fbc8283320fc74edb869ff1368cd11fe45af831c77125242`.

The full pipeline passed: 330 accepted / 0 rejected, zero overlap with all 31
protected benchmark fixtures, and a player/thread-disjoint 264/33/33 split.
`peva_voice_contextual.yaml` prepares a fresh-base, one-epoch QLoRA ablation:
rank 16, alpha 32, dropout 0.05 and LR `5e-5` are unchanged. One epoch is a
documented corrective change to avoid repeating the three-pass collapse. It is
only ready; it has not started.

Artifacts:

- Training report: `lastz_model_benchmark/training/outputs/reports/peva-voice-pilot-20261027.json`
- Base evaluation: `lastz_model_benchmark/training/outputs/reports/chat_checkpoint_evaluation_peva-voice-base-20261027.json`
- Adapter evaluation: `lastz_model_benchmark/training/outputs/reports/chat_checkpoint_evaluation_peva-voice-pilot-20261027.json`
- Saved outputs: `lastz_model_benchmark/training/outputs/evaluations/`
- Corrective data report: `lastz_model_benchmark/training/data/reports/peva_voice_contextual_readiness_report.json`
