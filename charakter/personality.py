"""Stable PEva identity; player adaptation never changes this baseline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "peva_personality.json"


@dataclass(frozen=True, slots=True)
class PevaPersonality:
    humor: float = 0.55
    playfulness: float = 0.70
    curiosity: float = 0.45
    teasing: float = 0.35
    supportiveness: float = 0.60
    energy: float = 0.55
    emoji_baseline: str = "LOW"
    default_reply_length: str = "SHORT"
    description: str = "casual, friendly, playful, slightly cheeky, confident game-chat style"

    def prompt_line(self) -> str:
        return "PEVA PERSONALITY: " + self.description + ". Keep replies natural, short, and never customer-support-like."


def load_personality(path: str | Path = DEFAULT_PATH) -> PevaPersonality:
    target = Path(path)
    if not target.is_file():
        return PevaPersonality()
    raw: Any = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Personality config must be an object: {target}")
    values: dict[str, Any] = {}
    for name in ("humor", "playfulness", "curiosity", "teasing", "supportiveness", "energy"):
        if name in raw:
            value = float(raw[name])
            if not 0.0 <= value <= 1.0: raise ValueError(f"{name} must be 0.0-1.0")
            values[name] = value
    for name, allowed in (("emoji_baseline", {"LOW", "MEDIUM", "HIGH"}), ("default_reply_length", {"SHORT", "NORMAL", "DETAILED"})):
        if name in raw:
            value = str(raw[name]).upper()
            if value not in allowed: raise ValueError(f"Unsupported {name}: {value}")
            values[name] = value
    if "description" in raw:
        values["description"] = str(raw["description"]).strip() or PevaPersonality.description
    return PevaPersonality(**values)
