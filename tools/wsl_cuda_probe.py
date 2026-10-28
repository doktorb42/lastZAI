"""Minimal no-model diagnostic for the dedicated WSL QLoRA environment."""

from __future__ import annotations

import platform


def emit(label: str, value: object) -> None:
    print(f"{label}={value}", flush=True)


emit("phase", "python_ready")
emit("platform", platform.platform())
emit("phase", "importing_torch")
import torch

emit("phase", "torch_imported")
emit("torch", torch.__version__)
emit("cuda_available", torch.cuda.is_available())
if torch.cuda.is_available():
    free, total = torch.cuda.mem_get_info(0)
    emit("gpu", torch.cuda.get_device_name(0))
    emit("free_vram_bytes", free)
    emit("total_vram_bytes", total)
emit("phase", "complete")
