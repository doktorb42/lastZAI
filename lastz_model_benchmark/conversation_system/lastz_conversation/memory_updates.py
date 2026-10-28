"""Strict parser/validator for PEva V2 model-proposed memory updates.

The model can propose a deliberately narrow JSON document.  It cannot express
SQL, a table name, a filesystem path, a real database ID, a routing ID, or an
arbitrary action.  Python owns alias resolution and every database mutation.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .contracts import (
    MAX_MEMORY_OPERATIONS,
    MEMORY_KINDS,
    MEMORY_OPERATION_NAMES,
    MEMORY_UPDATE_SCHEMA_VERSION,
    PROFILE_FIELDS,
    ContractError,
    parse_utc_timestamp,
    validate_model_safe_text,
)


MAX_OPERATIONS = MAX_MEMORY_OPERATIONS
_MESSAGE_REF_RE = re.compile(r"^m(?:0|[1-9]\d*)$")
_MEMORY_REF_RE = re.compile(r"^mem(?:0|[1-9]\d*)$")
_THREAD_REF_RE = re.compile(r"^thread(?:0|[1-9]\d*)$")
_PROMISE_REF_RE = re.compile(r"^promise(?:0|[1-9]\d*)$")
_MEMORY_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_SQL_OR_CODE_RE = re.compile(
    r"(?:\b(?:select\s+.+\s+from|insert\s+into|update\s+\S+\s+set|delete\s+from|"
    r"drop\s+(?:table|database)|alter\s+table|create\s+table|pragma\b|attach\s+database|"
    r"detach\s+database)\b|```|\b(?:sqlite|sqlalchemy)\b)",
    re.IGNORECASE,
)


class MemoryUpdateError(ContractError):
    """A memory proposal did not comply with the Python-owned V2 contract."""


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MemoryUpdateError(f"{label} must be an object")
    result = dict(value)
    if any(not isinstance(key, str) for key in result):
        raise MemoryUpdateError(f"{label} contains a non-string field name")
    return result


def _strict_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise MemoryUpdateError(f"{label} contains unsupported fields: {', '.join(sorted(unexpected))}")
    missing = allowed - set(value)
    if missing:
        raise MemoryUpdateError(f"{label} is missing required fields: {', '.join(sorted(missing))}")


def _text(value: Any, label: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise MemoryUpdateError(f"{label} must be a string")
    normalized = " ".join(value.strip().split())
    if not normalized and not allow_empty:
        raise MemoryUpdateError(f"{label} cannot be empty")
    if len(normalized) > maximum:
        raise MemoryUpdateError(f"{label} exceeds {maximum} characters")
    try:
        safe = validate_model_safe_text(normalized, label, maximum)
    except ContractError as exc:
        raise MemoryUpdateError(str(exc)) from exc
    if not isinstance(safe, str):  # Defensive: nullable=False above.
        raise MemoryUpdateError(f"{label} must be a string")
    if _SQL_OR_CODE_RE.search(safe):
        raise MemoryUpdateError(f"{label} must not contain SQL or code")
    return safe


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemoryUpdateError("confidence must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise MemoryUpdateError("confidence must be a finite number within 0..1")
    return result


def _alias(value: Any, label: str, allowed: set[str], pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise MemoryUpdateError(f"{label} must be a local model alias")
    if value not in allowed:
        raise MemoryUpdateError(f"{label} is not present in allowed_refs")
    return value


def _evidence(value: Any, allowed_message_refs: set[str]) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 8:
        raise MemoryUpdateError("evidence_message_refs must be a non-empty list of at most 8 aliases")
    result: list[str] = []
    for item in value:
        reference = _alias(item, "evidence_message_ref", allowed_message_refs, _MESSAGE_REF_RE)
        if reference in result:
            raise MemoryUpdateError("evidence_message_refs cannot contain duplicate aliases")
        result.append(reference)
    return result


def _future_timestamp(value: Any, label: str, *, now: datetime) -> str:
    if not isinstance(value, str):
        raise MemoryUpdateError(f"{label} must be an RFC-3339 timestamp")
    try:
        parsed = parse_utc_timestamp(value)
    except ContractError as exc:
        raise MemoryUpdateError(str(exc)) from exc
    if parsed <= now:
        raise MemoryUpdateError(f"{label} must be in the future")
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _expires_at(value: Any, *, temporary: bool, now: datetime) -> str | None:
    if value is None:
        if temporary:
            raise MemoryUpdateError("temporary memory requires expires_at")
        return None
    if not temporary:
        raise MemoryUpdateError("long_term memory expires_at must be null")
    return _future_timestamp(value, "expires_at", now=now)


def _optional_future_timestamp(value: Any, label: str, *, now: datetime) -> str | None:
    if value is None:
        return None
    return _future_timestamp(value, label, now=now)


def _alias_set(values: Iterable[str] | None, label: str, pattern: re.Pattern[str], *, require_nonempty: bool = False) -> set[str]:
    if values is None:
        raise MemoryUpdateError(f"allowed_refs.{label} is required")
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise MemoryUpdateError(f"allowed_refs.{label} must be an array of aliases")
    result: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise MemoryUpdateError(f"allowed_refs.{label} must contain only local aliases")
        result.add(item)
    if require_nonempty and not result:
        raise MemoryUpdateError(f"allowed_refs.{label} cannot be empty")
    return result


def _coerce_allowed_refs(
    *,
    allowed_refs: Mapping[str, Iterable[str]] | None,
    allowed_message_refs: Iterable[str] | None,
    allowed_memory_refs: Iterable[str] | None,
    allowed_thread_refs: Iterable[str] | None,
    allowed_promise_refs: Iterable[str] | None,
    # Deprecated keyword aliases remain accepted only when their *values* are
    # already V2 aliases.  Raw IDs are never converted or exposed.
    allowed_message_ids: Iterable[str] | None,
    allowed_memory_ids: Iterable[str] | None,
    allowed_thread_ids: Iterable[str] | None,
    allowed_promise_ids: Iterable[str] | None,
) -> tuple[set[str], set[str], set[str], set[str]]:
    supplied_new = any(
        value is not None
        for value in (allowed_message_refs, allowed_memory_refs, allowed_thread_refs, allowed_promise_refs)
    )
    supplied_legacy = any(
        value is not None
        for value in (allowed_message_ids, allowed_memory_ids, allowed_thread_ids, allowed_promise_ids)
    )
    if allowed_refs is not None and (supplied_new or supplied_legacy):
        raise MemoryUpdateError("supply allowed_refs or individual alias lists, not both")
    if supplied_new and supplied_legacy:
        raise MemoryUpdateError("cannot mix V2 alias lists with deprecated *_ids lists")

    if allowed_refs is not None:
        mapping = _require_object(allowed_refs, "allowed_refs")
        expected = {"message_refs", "memory_refs", "thread_refs", "promise_refs"}
        _strict_keys(mapping, expected, "allowed_refs")
        return (
            _alias_set(mapping["message_refs"], "message_refs", _MESSAGE_REF_RE, require_nonempty=True),
            _alias_set(mapping["memory_refs"], "memory_refs", _MEMORY_REF_RE),
            _alias_set(mapping["thread_refs"], "thread_refs", _THREAD_REF_RE),
            _alias_set(mapping["promise_refs"], "promise_refs", _PROMISE_REF_RE),
        )

    if supplied_legacy:
        allowed_message_refs = allowed_message_ids
        allowed_memory_refs = allowed_memory_ids
        allowed_thread_refs = allowed_thread_ids
        allowed_promise_refs = allowed_promise_ids
    return (
        _alias_set(allowed_message_refs, "message_refs", _MESSAGE_REF_RE, require_nonempty=True),
        _alias_set(allowed_memory_refs, "memory_refs", _MEMORY_REF_RE),
        _alias_set(allowed_thread_refs, "thread_refs", _THREAD_REF_RE),
        _alias_set(allowed_promise_refs, "promise_refs", _PROMISE_REF_RE),
    )


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MemoryUpdateError(f"model memory output contains duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise MemoryUpdateError(f"model memory output cannot contain JSON constant {value!r}")


@dataclass(frozen=True)
class ValidatedMemoryUpdate:
    """Narrow canonical proposal safe for repository-owned application."""

    operations: tuple[dict[str, Any], ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": MEMORY_UPDATE_SCHEMA_VERSION,
            "operations": copy.deepcopy(list(self.operations)),
        }


def validate_memory_update_payload(
    payload: Mapping[str, Any],
    *,
    allowed_refs: Mapping[str, Iterable[str]] | None = None,
    allowed_message_refs: Iterable[str] | None = None,
    allowed_memory_refs: Iterable[str] | None = None,
    allowed_thread_refs: Iterable[str] | None = None,
    allowed_promise_refs: Iterable[str] | None = None,
    allowed_message_ids: Iterable[str] | None = None,
    allowed_memory_ids: Iterable[str] | None = None,
    allowed_thread_ids: Iterable[str] | None = None,
    allowed_promise_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> ValidatedMemoryUpdate:
    """Validate an already-decoded ``peva-memory-update-v2`` object.

    The only identifiers accepted in an operation are the task-local aliases
    supplied in ``allowed_refs``.  The repository resolves those aliases to
    internal identifiers after this function has returned successfully.
    """

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root = _require_object(payload, "memory update")
    _strict_keys(root, {"schema_version", "operations"}, "memory update")
    if root["schema_version"] != MEMORY_UPDATE_SCHEMA_VERSION:
        raise MemoryUpdateError("memory update has an unsupported schema_version")
    operations = root["operations"]
    if not isinstance(operations, list) or not operations or len(operations) > MAX_OPERATIONS:
        raise MemoryUpdateError(f"operations must contain 1-{MAX_OPERATIONS} items")
    allowed_messages, allowed_memories, allowed_threads, allowed_promises = _coerce_allowed_refs(
        allowed_refs=allowed_refs,
        allowed_message_refs=allowed_message_refs,
        allowed_memory_refs=allowed_memory_refs,
        allowed_thread_refs=allowed_thread_refs,
        allowed_promise_refs=allowed_promise_refs,
        allowed_message_ids=allowed_message_ids,
        allowed_memory_ids=allowed_memory_ids,
        allowed_thread_ids=allowed_thread_ids,
        allowed_promise_ids=allowed_promise_ids,
    )

    validated: list[dict[str, Any]] = []
    for ordinal, raw_operation in enumerate(operations, 1):
        operation = _require_object(raw_operation, f"operation {ordinal}")
        op = operation.get("op")
        if not isinstance(op, str) or op not in MEMORY_OPERATION_NAMES:
            raise MemoryUpdateError(f"operation {ordinal} has unsupported op {op!r}")

        if op == "noop":
            _strict_keys(operation, {"op", "reason"}, "noop")
            validated.append({"op": "noop", "reason": _text(operation["reason"], "noop reason", 200)})
            continue

        if op == "upsert_memory":
            _strict_keys(
                operation,
                {"op", "kind", "key", "value", "scope", "confidence", "expires_at", "evidence_message_refs"},
                "upsert_memory",
            )
            kind = _text(operation["kind"], "memory kind", 40)
            if kind not in MEMORY_KINDS:
                raise MemoryUpdateError("memory kind is not allowed")
            key = _text(operation["key"], "memory key", 80)
            if not _MEMORY_KEY_RE.fullmatch(key):
                raise MemoryUpdateError("memory key must use lowercase dotted/slashed-free identifier syntax")
            scope = _text(operation["scope"], "memory scope", 20)
            if scope not in {"long_term", "temporary"}:
                raise MemoryUpdateError("memory scope must be long_term or temporary")
            validated.append(
                {
                    "op": op,
                    "kind": kind,
                    "key": key,
                    "value": _text(operation["value"], "memory value", 300),
                    "scope": scope,
                    "confidence": _confidence(operation["confidence"]),
                    "expires_at": _expires_at(operation["expires_at"], temporary=scope == "temporary", now=current_time),
                    "evidence_message_refs": _evidence(operation["evidence_message_refs"], allowed_messages),
                }
            )
            continue

        if op == "expire_memory":
            _strict_keys(operation, {"op", "memory_ref", "reason", "evidence_message_refs"}, "expire_memory")
            validated.append(
                {
                    "op": op,
                    "memory_ref": _alias(operation["memory_ref"], "memory_ref", allowed_memories, _MEMORY_REF_RE),
                    "reason": _text(operation["reason"], "expiry reason", 200),
                    "evidence_message_refs": _evidence(operation["evidence_message_refs"], allowed_messages),
                }
            )
            continue

        if op == "set_profile_field":
            _strict_keys(operation, {"op", "field", "value", "confidence", "evidence_message_refs"}, "set_profile_field")
            field = _text(operation["field"], "profile field", 40)
            if field not in PROFILE_FIELDS:
                raise MemoryUpdateError("profile field is not allowed")
            validated.append(
                {
                    "op": op,
                    "field": field,
                    "value": _text(operation["value"], "profile value", 160),
                    "confidence": _confidence(operation["confidence"]),
                    "evidence_message_refs": _evidence(operation["evidence_message_refs"], allowed_messages),
                }
            )
            continue

        if op == "open_thread":
            _strict_keys(
                operation,
                {"op", "title", "promise", "due_at", "confidence", "evidence_message_refs"},
                "open_thread",
            )
            validated.append(
                {
                    "op": op,
                    "title": _text(operation["title"], "thread title", 160),
                    "promise": _text(operation["promise"], "thread promise", 300),
                    "due_at": _optional_future_timestamp(operation["due_at"], "thread due_at", now=current_time),
                    "confidence": _confidence(operation["confidence"]),
                    "evidence_message_refs": _evidence(operation["evidence_message_refs"], allowed_messages),
                }
            )
            continue

        if op == "close_thread":
            _strict_keys(operation, {"op", "thread_ref", "reason", "evidence_message_refs"}, "close_thread")
            validated.append(
                {
                    "op": op,
                    "thread_ref": _alias(operation["thread_ref"], "thread_ref", allowed_threads, _THREAD_REF_RE),
                    "reason": _text(operation["reason"], "thread close reason", 200),
                    "evidence_message_refs": _evidence(operation["evidence_message_refs"], allowed_messages),
                }
            )
            continue

        if op == "open_promise":
            _strict_keys(operation, {"op", "text", "due_at", "confidence", "evidence_message_refs"}, "open_promise")
            validated.append(
                {
                    "op": op,
                    "text": _text(operation["text"], "promise text", 300),
                    "due_at": _optional_future_timestamp(operation["due_at"], "promise due_at", now=current_time),
                    "confidence": _confidence(operation["confidence"]),
                    "evidence_message_refs": _evidence(operation["evidence_message_refs"], allowed_messages),
                }
            )
            continue

        if op == "close_promise":
            _strict_keys(operation, {"op", "promise_ref", "reason", "evidence_message_refs"}, "close_promise")
            validated.append(
                {
                    "op": op,
                    "promise_ref": _alias(operation["promise_ref"], "promise_ref", allowed_promises, _PROMISE_REF_RE),
                    "reason": _text(operation["reason"], "promise close reason", 200),
                    "evidence_message_refs": _evidence(operation["evidence_message_refs"], allowed_messages),
                }
            )
            continue

        # Kept defensive even though op membership above is exhaustive.
        raise MemoryUpdateError(f"operation {ordinal} has unsupported op {op!r}")

    if any(operation["op"] == "noop" for operation in validated) and len(validated) != 1:
        raise MemoryUpdateError("noop must be the only operation in an update")
    return ValidatedMemoryUpdate(tuple(validated))


def parse_and_validate_memory_update(
    raw: str,
    *,
    allowed_refs: Mapping[str, Iterable[str]] | None = None,
    allowed_message_refs: Iterable[str] | None = None,
    allowed_memory_refs: Iterable[str] | None = None,
    allowed_thread_refs: Iterable[str] | None = None,
    allowed_promise_refs: Iterable[str] | None = None,
    allowed_message_ids: Iterable[str] | None = None,
    allowed_memory_ids: Iterable[str] | None = None,
    allowed_thread_ids: Iterable[str] | None = None,
    allowed_promise_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> ValidatedMemoryUpdate:
    """Parse strict JSON and reject every operation outside the V2 allow-list."""

    if not isinstance(raw, str) or not raw.strip():
        raise MemoryUpdateError("model memory output must be non-empty JSON text")
    stripped = raw.strip()
    if stripped.startswith(chr(96) * 3):
        raise MemoryUpdateError("model memory output must not be fenced Markdown")
    try:
        payload = json.loads(stripped, object_pairs_hook=_unique_object_pairs, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise MemoryUpdateError(f"model memory output is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, Mapping):
        raise MemoryUpdateError("memory update must be an object")
    return validate_memory_update_payload(
        payload,
        allowed_refs=allowed_refs,
        allowed_message_refs=allowed_message_refs,
        allowed_memory_refs=allowed_memory_refs,
        allowed_thread_refs=allowed_thread_refs,
        allowed_promise_refs=allowed_promise_refs,
        allowed_message_ids=allowed_message_ids,
        allowed_memory_ids=allowed_memory_ids,
        allowed_thread_ids=allowed_thread_ids,
        allowed_promise_ids=allowed_promise_ids,
        now=now,
    )
