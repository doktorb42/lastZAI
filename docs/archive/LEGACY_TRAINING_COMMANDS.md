# Archiwum — dawne techniczne komendy Agency

Ten dokument zachowuje wcześniejsze polecenia Review Studio i QLoRA. Nie są
one częścią zwykłego użycia PEva ani instrukcją automatycznego treningu. Zostały
przeniesione z głównego `README.md`, aby normalne uruchomienie pozostało proste.

## Agency Review Studio

The local Review Studio keeps source datasets immutable and stores review state,
revision history, and automatic backups under `data\training\agency\review\`.
It has no internet dependency and never starts QLoRA itself.

```powershell
# Start the local-only Studio, then open the printed http://127.0.0.1:8765 URL.
py -B agency_review_studio.py --serve

# Validate the saved/legacy review document, source schemas, safety rules,
# duplicate candidates, and stable train/validation assignment.
py -B agency_review_studio.py --validate

# Optionally load local game-mechanics review rules instead of the safe defaults.
py -B agency_review_studio.py --game-rules path\to\lastz_review_rules.json --validate

# Preview every output file and blocker without writing a dataset version.
py -B agency_review_studio.py --prepare reviewed-v1

# Export a normalized, portable review JSON document.
py -B agency_review_studio.py --export data\training\agency\review\my_review_export.json
```

Only after the Studio reports readiness and you explicitly want to write a new
version (never the original source files), use `--prepare <version> --apply`.
The Studio preserves existing split membership, excludes only `DROP` records,
and blocks on unresolved reviews, validation errors, exact duplicates, or split
leakage. Run the frozen base benchmark from the same WSL `.venv-agency` environment:

```powershell
wsl -d Ubuntu --cd /mnt/d/projekty/lastZAI .venv-agency/bin/python -B agency_benchmark.py --base
```

## Agency QLoRA pilot training (Windows + WSL)

`train_peva_agency_pilot.py --run` is deliberately blocked when it is launched
by Windows Python (`py ...`). The actual QLoRA run must use the Linux Python
in the project WSL environment, `.venv-agency`, where CUDA, PyTorch,
bitsandbytes, PEFT, and Transformers are available. This is intentional: it
prevents a Windows-only environment from starting or partially writing a
training run.

Run these commands in **PowerShell**. Replace `Ubuntu` only if `wsl -l -v`
shows a different WSL distribution name.

```powershell
# List installed WSL distributions and confirm that Ubuntu uses WSL version 2.
wsl -l -v

# Confirm that the WSL environment can see the NVIDIA GPU.
wsl -d Ubuntu -- nvidia-smi

# Required first step: performs all Agency dataset, benchmark, dependency,
# CUDA, VRAM, and output-directory checks. It does not train or write a run.
wsl -d Ubuntu --cd /mnt/d/projekty/lastZAI .venv-agency/bin/python -B train_peva_agency_pilot.py --dry-run
```

Start a real pilot only when the dry-run JSON says `"ready": true`:

```powershell
# Starts exactly one guarded QLoRA Agency pilot in WSL/Linux.
wsl -d Ubuntu --cd /mnt/d/projekty/lastZAI .venv-agency/bin/python -B train_peva_agency_pilot.py --run
```

The same commands can be run from an interactive WSL shell:

```bash
cd /mnt/d/projekty/lastZAI
source .venv-agency/bin/activate
python -B train_peva_agency_pilot.py --dry-run
python -B train_peva_agency_pilot.py --run
```

The PowerShell commands intentionally call `.venv-agency/bin/python`
directly, so manual activation is not needed there.

Do **not** use a GGUF model as training input. The pilot always trains the
pinned `Qwen/Qwen2.5-7B-Instruct` Transformers checkpoint with 4-bit NF4
QLoRA; GGUF/Q6_K is an inference/export stage after a successful adapter run.
If the dry run reports `ready: false`, fix the listed blocker first. In
particular, the quality/pilot gate intentionally blocks training until the
Agency dataset requirements are satisfied.
