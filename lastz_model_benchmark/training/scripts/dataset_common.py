#!/usr/bin/env python3
"""Small stdlib-only helpers shared by the Last Z synthetic-data scripts."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCRIPTS_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPTS_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
BENCHMARK_CASES_DIR = BUNDLE_ROOT / "benchmarks" / "cases"
DATA_ROOT = TRAINING_ROOT / "data"


class DatasetError(RuntimeError):
    """Raised for an actionable, user-facing dataset pipeline failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def safe_within_training(path: Path | str) -> Path:
    """Resolve a pipeline output and keep it inside lastz_model_benchmark/training."""

    candidate = Path(path).expanduser().resolve()
    root = TRAINING_ROOT.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DatasetError(
            f"refusing to write outside the isolated training folder: {candidate}"
        ) from exc
    return candidate


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise DatasetError(f"JSONL file not found: {source}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSON at {source}:{line_number}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise DatasetError(f"JSONL row at {source}:{line_number} must be an object")
        rows.append(row)
    return rows


def write_jsonl(path: Path | str, rows: Iterable[dict[str, Any]]) -> Path:
    destination = safe_within_training(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement prevents half-written artifacts from becoming an
    # accidental training input if a later validator fails.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", delete=False, dir=destination.parent,
        prefix=f".{destination.name}.", suffix=".tmp",
    ) as handle:
        temporary = Path(handle.name)
        for row in rows:
            # Preserve insertion order in the persisted record.  The action
            # prompt intentionally follows the benchmark's insertion-order
            # JSON rendering; recursively sorting here would mutate a saved
            # state's key order and make its already-rendered prompt fail a
            # round-trip validation after reload.  Canonical JSON remains
            # available separately for fingerprints and hashes.
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    os.replace(temporary, destination)
    return destination


def write_json(path: Path | str, payload: dict[str, Any]) -> Path:
    destination = safe_within_training(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", delete=False, dir=destination.parent,
        prefix=f".{destination.name}.", suffix=".tmp",
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    os.replace(temporary, destination)
    return destination


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalise_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


_TOKEN_RE = re.compile(r"[a-z0-9_]+", flags=re.IGNORECASE)


def token_set(value: Any) -> set[str]:
    return set(_TOKEN_RE.findall(normalise_text(value)))


def jaccard_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    first, second = set(left), set(right)
    if not first and not second:
        return 1.0
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def character_ngrams(value: Any, size: int = 5) -> set[str]:
    text = normalise_text(value)
    if len(text) <= size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def count_values(rows: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    values = Counter(str(row.get(field, "<missing>")) for row in rows)
    return dict(sorted(values.items()))


def add_rejection(row: dict[str, Any], reasons: Iterable[str], stage: str) -> dict[str, Any]:
    rejected = dict(row)
    prior = rejected.get("rejection_reasons")
    all_reasons = list(prior) if isinstance(prior, list) else []
    all_reasons.extend(str(reason) for reason in reasons)
    rejected["rejection_reasons"] = list(dict.fromkeys(all_reasons))
    rejected["rejection_stage"] = stage
    return rejected
