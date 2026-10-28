# Last Z fine-tuning pipeline

Everything in this pipeline is intentionally contained in
`lastz_model_benchmark/training/`.  It is offline with respect to Last Z: it
does not import ADB, start BlueStacks, take screenshots, or execute a game
action.

## Phase order

1. Inspect the environment and select the pinned QLoRA checkpoint.
2. Generate and validate Action V1 data, reject duplicates and benchmark
   leakage, then split by scenario family.
3. Run the mandatory 10-step CUDA QLoRA smoke test.
4. Train and evaluate Action V1 against the protected benchmark.
5. Merge the selected adapter, export HF -> F16 GGUF -> Q6_K, and run the
   Windows CUDA benchmark.
6. Only after Action V1 clears the production gate, collect/review Chat V1
   data and then consider multitask experiments.

Action V1 uses the same system/user action contract as the extended benchmark.
The task is distinguished by the action system prompt, not by a training-only
marker that would create a prompt mismatch.  The model can return the virtual
planner intent `reply_private`; raw `send_message` and message text remain
outside the action model and must be validated by Python later.

## Layout

```text
configs/       versioned experiment configurations (JSON-compatible YAML)
data/raw/      externally supplied or unverified records
data/generated/ deterministic synthetic candidates
data/reviewed/ validation, deduplication, and leakage-clean intermediates
data/processed/ split, training-ready prompt/completion JSONL
data/rejected/ rejected records plus reasons
scripts/       all pipeline CLIs
outputs/       adapters, merged HF models, GGUFs, and reports
```

Generated candidates never become training data merely because they exist.
The required path is: validate -> deduplicate -> holdout leakage check ->
family-isolated split -> hash-backed quality gate.  The trainer refuses to run
without that gate.

## First safe commands

From the project root, use the desired Python explicitly.  The environment
checker is read-only except for writing its report inside this directory.

```powershell
py -3.12 .\lastz_model_benchmark\training\scripts\inspect_environment.py
py -3.12 .\lastz_model_benchmark\training\scripts\select_model.py --verify-remote
py -3.12 .\lastz_model_benchmark\training\scripts\generate_action_dataset.py --count 2400
```

Then follow the command sequence printed by the generator report.  Do not
install a training stack or download weights until the environment checker has
reported CUDA-ready status.  `select_model.py --download` is explicit and
never runs by default.

The WSL setup wrapper is dry-run by default and creates no environment until
the explicit `--execute` flag is used:

```powershell
py -3.12 .\lastz_model_benchmark\training\scripts\bootstrap_wsl_training.py
```

### WSL CUDA training environment

For this RTX 3050 / driver 577 setup, `requirements-wsl.txt` deliberately
uses the current Unsloth CUDA 12.6 / Torch 2.11 extra, not generic CUDA 13
wheels.  The executable environment remains inside this bundle at
`training/.venv-wsl-cu126`.

```powershell
py -3.12 .\lastz_model_benchmark\training\scripts\bootstrap_wsl_training.py --execute
wsl.exe --distribution Ubuntu -- /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/.venv-wsl-cu126/bin/python /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/scripts/inspect_environment.py --strict --min-free-vram-gib 5
wsl.exe --distribution Ubuntu -- /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/.venv-wsl-cu126/bin/python /mnt/d/projekty/lastZAI/lastz_model_benchmark/training/scripts/select_model.py --resolve --download
```

The trainer accepts `--model-path models/hf/qwen2_5_3b_unsloth_bnb_4bit_action_v1`
to use that reviewed local checkpoint offline.  It rejects model/adaptor/output
paths outside `training/`, GGUF training inputs, and CPU fallback.

## Action V1 gate

Training loss is not a success criterion.  The protected production suite
must reach **100% schema-valid, legal, and exact decisions**.  Adversarial
robustness is reported separately and must never reduce or inflate production
qualification.  All benchmark calls use synthetic local fixtures only.

After running separate completed base and adapter evaluations, compare their
full-holdout reports without loading a model again:

```powershell
py -3.12 .\lastz_model_benchmark\training\scripts\compare_action_evaluations.py --base-report checkpoint_evaluation_BASE.json --fine-report checkpoint_evaluation_FINE.json
```

The comparison CLI rejects sampled/limited reports, missing current fixtures,
or mismatched rounds, seed, and decoding limits.

## Export and production guardrails

Merge an accepted adapter into the matching FP16/BF16 Hugging Face base, then
convert it to F16 GGUF and quantize that source to Q6_K with the pinned
llama.cpp tooling.  Never requantize a Q4 GGUF to Q6_K.

Q6_K is the primary candidate, not an automatic production winner.  The final
CUDA benchmark must check VRAM with the model loaded before any separate,
manual BlueStacks coexistence test.  This pipeline never launches BlueStacks
or sends a message.

For a like-for-like final comparison, generate the base Q6_K independently
from the pinned full Hugging Face merge base using
`scripts/export_base_gguf.py`.  It validates the exact cached revision and
uses the same `HF safetensors -> F16 GGUF -> Q6_K` flow as the adapted export;
it never promotes an existing Q4 artifact to Q6_K.  When running the converter
from WSL with the Windows CUDA quantizer, both export scripts translate data
paths explicitly for the `.exe` process.
