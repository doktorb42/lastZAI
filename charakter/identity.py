"""Stable, non-numeric PEva identity loaded from a reviewable JSON file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "peva_self.json"


@dataclass(frozen=True, slots=True)
class PevaSelf:
    name: str
    role: str
    identity: tuple[str, ...]
    conversation_rules: tuple[str, ...]

    def prompt_block(self) -> str:
        lines = [f"PEVA SELF\nName: {self.name}\nRole: {self.role}", "IDENTITY:"]
        lines.extend(f"- {item}" for item in self.identity)
        lines.append("CONVERSATION RULES:")
        lines.extend(f"- {item}" for item in self.conversation_rules)
        return "\n".join(lines)


def load_peva_self(path: str | Path = DEFAULT_PATH) -> PevaSelf:
    target = Path(path)
    try:
        raw: Any = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read PEva self config: {target}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid PEva self JSON: {target}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("PEva self config must be an object.")
    name = _text(raw.get("name"), "name")
    role = _text(raw.get("role"), "role")
    identity = _lines(raw.get("identity"), "identity")
    rules = _lines(raw.get("conversation_rules"), "conversation_rules")
    return PevaSelf(name=name, role=role, identity=identity, conversation_rules=rules)


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"PEva self {field} must be non-empty.")
    return result


def _lines(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"PEva self {field} must be an array.")
    result = tuple(str(item).strip() for item in value if str(item).strip())
    if not result:
        raise ValueError(f"PEva self {field} must not be empty.")
    return result
