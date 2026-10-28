# Action V1 model selection

## Selected training checkpoint

| Purpose | Value |
| --- | --- |
| QLoRA checkpoint | `unsloth/Qwen2.5-3B-Instruct-unsloth-bnb-4bit` |
| Pinned revision | `21d7790b1332196a5cf65ba72f2b46b659f74ddf` |
| Source | <https://huggingface.co/unsloth/Qwen2.5-3B-Instruct-unsloth-bnb-4bit> |
| Architecture | `Qwen2ForCausalLM`, Qwen2.5 3B Instruct |
| Training representation | Unsloth dynamic 4-bit / bitsandbytes NF4, double quantization |
| Upstream instruction model | `Qwen/Qwen2.5-3B-Instruct` at `aa8e72537993ba99e69dfaafa59ed015b17504d1` |
| Matching merge base | `unsloth/Qwen2.5-3B-Instruct` at `7548fff1f997f57b2e9e8ab1ec7be96949b00ed0` |

This is deliberately a Transformers/Safetensors checkpoint.  The existing
`qwen2.5-3b-instruct-q4_k_m.gguf` is inference-only and must never be used as
the training base or requantized into the final model.

## Why this checkpoint

Unsloth's current model mapper lists the `-unsloth-bnb-4bit` Qwen2.5 3B
Instruct checkpoint and maps it to the upstream instruction model.  Its model
card identifies it as Unsloth dynamic 4-bit: a selectively higher-precision
variant intended to preserve more quality than the older plain
`unsloth/Qwen2.5-3B-Instruct-bnb-4bit` checkpoint at a small VRAM cost.  That
trade-off is appropriate for a safety-critical, very small action vocabulary
on an 8 GB GPU.

Qwen2.5 3B Instruct is retained because the requested production family is
Qwen2.5-3B-Instruct, it has a native ChatML chat template, and Qwen documents
improvements for structured/JSON output.  We do not switch families silently.

## Reproducible training stack

The first validated WSL lock candidate is:

```text
Python 3.11
torch 2.11.0+cu126
unsloth[cu126-torch2110] == 2026.10.3
unsloth-zoo == 2026.10.3
transformers >= 4.52.4, <= 5.17.0
trl >= 0.18.2, <= 1.13.0, != 0.19
peft >= 0.18.0, != 0.11
accelerate >= 0.34.1
datasets >= 3.4.1, < 5
bitsandbytes >= 0.45.5, != 0.46.0, != 0.48.0
```

The requirements file supplies the PyTorch CUDA 12.6 index explicitly.  This
is intentional: the machine's R577 driver meets CUDA 12.x compatibility but
not CUDA 13.x's R580 minimum, so an unqualified resolver must not silently
choose CUDA 13 wheels.

`inspect_environment.py` must first verify the WSL NVIDIA driver and CUDA
runtime.  If the driver does not support CUDA 12.6, select the current
Unsloth extra matching the detected Torch/CUDA combination instead; do not
fall back to CPU.  After the smoke run, record the resolver's exact `pip
freeze` output in the smoke report.

The action trainer uses a conversational prompt/completion dataset and
`completion_only_loss=True`.  Qwen2.5's native template does not provide TRL
generation markers required for `assistant_only_loss=True`, so the latter is
not used.

## Hardware estimate and limits

The 3B dynamic-4-bit QLoRA checkpoint is realistic for this RTX 3050 8 GB at
1024 tokens, but the estimate is not a guarantee.  Start with batch size 1,
gradient accumulation 16, rank 16, and Unsloth gradient checkpointing.  A
10-step CUDA smoke test is a mandatory gate and records allocated/reserved and
peak VRAM, host RAM, loss, and step time.  Batch size 2 is considered only if
that measured peak leaves practical headroom.

The local Windows Python 3.12 installation currently sees the RTX 3050 and
CUDA.  A subsequent isolated WSL probe also passed: Ubuntu WSL2 exposes Python
3.11 and 3.12 plus the RTX 3050 through `nvidia-smi`.  Use Python 3.11 in a
project-local WSL virtual environment for the pinned first experiment.  The
training packages and checkpoint are not installed/downloaded yet; that is the
remaining pre-smoke setup work, not a CUDA/WSL blocker.  The pipeline never
conceals a CUDA failure by switching to CPU.

## License note

The Unsloth repository card advertises Apache-2.0, while the upstream Qwen
repository distributes the source weights under the Qwen Research License.
Treat the upstream terms as controlling for derivative use; its license says
commercial use requires a separate Alibaba license.  This pipeline does not
change that obligation.

## Sources checked on 2026-10-10

- [Official Qwen model card](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)
- [Official Qwen license](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/raw/main/LICENSE)
- [Official Unsloth dynamic 4-bit checkpoint](https://huggingface.co/unsloth/Qwen2.5-3B-Instruct-unsloth-bnb-4bit)
- [Unsloth model mapper](https://github.com/unslothai/unsloth/blob/main/unsloth/models/mapper.py)
- [Unsloth installation guidance](https://docs.unsloth.ai/get-started/installing-%2B-updating/pip-install)
- [Unsloth LoRA guidance](https://unsloth.ai/docs/get-started/fine-tuning-llms-guide/lora-hyperparameters-guide)
- [TRL SFT trainer documentation](https://huggingface.co/docs/trl/en/sft_trainer)
- [Transformers bitsandbytes documentation](https://huggingface.co/docs/transformers/en/quantization/bitsandbytes)
