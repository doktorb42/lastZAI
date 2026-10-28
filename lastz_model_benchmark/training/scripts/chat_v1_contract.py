#!/usr/bin/env python3
"""Canonical, reviewable source-record contract for PEva Chat V1.

This module deliberately sits between the immutable PEva V2 conversation
export and a future SFT dataset.  It does *not* create a new prompt format:
the only model-facing prompt is the exact ``<TASK_CHAT>`` / ``peva-chat-input-v2``
snapshot emitted by :mod:`conversation_system`.

The source record is intentionally more verbose than the eventual SFT row so
that human review, provenance, draft/edited-reply separation, and split groups
are auditable without leaking any of that metadata into the model prompt.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
PROJECT_ROOT = BUNDLE_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import (  # noqa: E402
    CHAT_PROMPT_VERSION,
    CONTEXT_CONTRACT_VERSION,
    TASK_CHAT,
    ContractError,
    canonical_json as contract_canonical_json,
    render_chat_messages,
    validate_model_safe_text,
    validate_task_chat_input,
)

from chat_quality import MAX_NORMAL_REPLY_CHARACTERS, validate_player_ready_output  # noqa: E402


SOURCE_RECORD_VERSION = "peva-chat-v1-source-record-v1"
SOURCE_RECORD_TYPE = "peva_chat_v1_source_record"
REVIEW_EXPORT_RECORD_TYPE = "peva_v2_approved_chat_review_record"

SOURCE_TYPES = frozenset({"real_approved", "manually_authored", "synthetic_candidate"})
APPROVAL_STATES = frozenset({"unreviewed", "approved", "rejected"})
CATEGORIES = frozenset(
    {
        "casual_greeting",
        "casual_conversation",
        "alliance_coordination",
        "event_discussion",
        "strategy_game",
        "scheduling",
        "humor_teasing",
        "short_reaction",
        "disagreement_conflict",
        "clarification",
        "unknown_information",
        "other_reviewed",
    }
)

# The requested Last Z reply bands deliberately preserve a small explicit
# social exception.  A one-word "hey" or "nice" is valid in a real private
# conversation, while the four configured bands retain coverage for ordinary
# 5-120 word replies.  Every approved source record must declare one band and
# fit it; the declaration is review metadata, never model-visible prompt text.
LENGTH_BANDS: dict[str, tuple[int, int]] = {
    "micro_social": (1, 4),
    "very_short": (5, 20),
    "short": (15, 35),
    "medium": (35, 70),
    "occasional_long": (70, 120),
}
_REPLY_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

_PLAYER_GROUP_RE = re.compile(r"^pgrp_[a-z0-9][a-z0-9_-]{0,95}$")
_THREAD_GROUP_RE = re.compile(r"^tgrp_[a-z0-9][a-z0-9_-]{0,95}$")
_VARIANT_GROUP_RE = re.compile(r"^vgrp_[a-z0-9][a-z0-9_-]{0,95}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    flags=re.IGNORECASE,
)
_RAW_ID_TEXT_RE = re.compile(
    r"\b(?:uid|player[ _-]?id|game[ _-]?player[ _-]?id|room[ _-]?id|"
    r"conversation[ _-]?id|turn[ _-]?id|snapshot[ _-]?id|sqlite[ _-]?id)\b"
    r"\s*(?:[:=#-]|is)?\s*[A-Za-z0-9][A-Za-z0-9_.:-]{2,}",
    flags=re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"(?:^|\s)[A-Za-z]:[\\/][^\s\"']+")
_UNC_PATH_RE = re.compile(r"(?:^|\s)\\\\[^\s\\/]+[\\/][^\s\"']+")
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "uid",
        "player_id",
        "game_player_id",
        "peva_game_player_id",
        "conversation_id",
        "room_id",
        "external_room_id",
        "message_id",
        "memory_id",
        "thread_id",
        "promise_id",
        "summary_id",
        "turn_id",
        "snapshot_id",
        "attempt_id",
        "source_message_id",
        "source_message_key",
        "routing_id",
        "external_id",
        "database_id",
        "sqlite_id",
        "uuid",
        "path",
        "file_path",
        "database_path",
        "routing",
    }
)


class ChatV1ContractError(ValueError):
    """A review record cannot safely enter the Chat V1 data pipeline."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """Canonical JSON delegated to the V2 contract implementation."""

    return contract_canonical_json(value)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def _strict_object(value: Any, *, required: set[str], allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ChatV1ContractError(f"{label} must be an object")
    result = dict(value)
    if any(not isinstance(key, str) for key in result):
        raise ChatV1ContractError(f"{label} has a non-string key")
    unexpected = set(result) - allowed
    if unexpected:
        raise ChatV1ContractError(f"{label} has unsupported fields: {', '.join(sorted(unexpected))}")
    missing = required - set(result)
    if missing:
        raise ChatV1ContractError(f"{label} is missing required fields: {', '.join(sorted(missing))}")
    return result


def _bounded_text(value: Any, label: str, maximum: int, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ChatV1ContractError(f"{label} must be a string" + (" or null" if nullable else ""))
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        raise ChatV1ContractError(f"{label} cannot be empty")
    if len(text) > maximum:
        raise ChatV1ContractError(f"{label} exceeds {maximum} characters")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise ChatV1ContractError(f"{label} contains a control character")
    return text


def reply_word_count(value: str) -> int:
    """Count English-style reply words deterministically for band validation."""

    return len(_REPLY_WORD_RE.findall(value))


def validate_length_band(value: Any, *, reply: str | None = None, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ChatV1ContractError("approved records require length_band")
        return None
    if not isinstance(value, str) or value not in LENGTH_BANDS:
        raise ChatV1ContractError(f"length_band must be null or one of {sorted(LENGTH_BANDS)}")
    if reply is not None:
        count = reply_word_count(reply)
        minimum, maximum = LENGTH_BANDS[value]
        if not minimum <= count <= maximum:
            raise ChatV1ContractError(
                f"approved_reply has {count} words but length_band {value!r} requires {minimum}-{maximum}"
            )
    return value


def _canonical_timestamp(value: Any, label: str, *, nullable: bool = False) -> str | None:
    text = _bounded_text(value, label, 64, nullable=nullable)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ChatV1ContractError(f"{label} must be RFC-3339") from exc
    if parsed.tzinfo is None:
        raise ChatV1ContractError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _leak_errors(value: Any, *, label: str = "record") -> list[str]:
    """Detect runtime/storage data outside the V2 model contract.

    The V2 contract already protects ``task_snapshot.input``.  This extra scan
    protects raw drafts, approved targets, and review metadata from becoming a
    convenient route for an accidental UID/room/SQLite value to reach a later
    formatter or human export.
    """

    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                errors.append(f"{label} contains a non-string key")
                continue
            folded = key.casefold()
            # ``record_id`` is a pipeline-local opaque hash, not a runtime
            # identifier.  Every other ``*_id`` field is rejected so a route,
            # room, SQLite, or game identity cannot be smuggled through a
            # review record as harmless-looking provenance.
            if folded in _FORBIDDEN_METADATA_KEYS or (folded.endswith("_id") and folded != "record_id"):
                errors.append(f"{label} exposes forbidden runtime/storage field {key!r}")
            errors.extend(_leak_errors(nested, label=f"{label}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            errors.extend(_leak_errors(nested, label=f"{label}[{index}]"))
    elif isinstance(value, str):
        if _UUID_RE.search(value):
            errors.append(f"{label} exposes a UUID")
        if _RAW_ID_TEXT_RE.search(value):
            errors.append(f"{label} exposes a raw runtime identifier")
        if _WINDOWS_PATH_RE.search(value) or _UNC_PATH_RE.search(value):
            errors.append(f"{label} exposes a filesystem path")
    return list(dict.fromkeys(errors))


def _opaque_group(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ChatV1ContractError(
            f"{label} must be an opaque alias matching {pattern.pattern}; never use a Last Z UID, room ID, or SQLite ID"
        )
    return value


def validate_grouping(value: Any) -> dict[str, str]:
    group = _strict_object(value, required={"player_group", "thread_group"}, allowed={"player_group", "thread_group"}, label="grouping")
    return {
        "player_group": _opaque_group(group["player_group"], _PLAYER_GROUP_RE, "grouping.player_group"),
        "thread_group": _opaque_group(group["thread_group"], _THREAD_GROUP_RE, "grouping.thread_group"),
    }


def _validate_provenance(value: Any) -> dict[str, Any]:
    source = _strict_object(
        value,
        required={"origin", "source_ref"},
        allowed={"origin", "source_ref", "captured_at", "notes"},
        label="provenance",
    )
    origin = _bounded_text(source["origin"], "provenance.origin", 80)
    source_ref = _bounded_text(source["source_ref"], "provenance.source_ref", 160)
    captured_at = _canonical_timestamp(source.get("captured_at"), "provenance.captured_at", nullable=True)
    notes = _bounded_text(source.get("notes"), "provenance.notes", 500, nullable=True)
    result: dict[str, Any] = {"origin": origin, "source_ref": source_ref}
    if captured_at is not None:
        result["captured_at"] = captured_at
    if notes is not None:
        result["notes"] = notes
    leaks = _leak_errors(result, label="provenance")
    if leaks:
        raise ChatV1ContractError("; ".join(leaks))
    return result


def _validate_approval(value: Any, *, source_type: str) -> dict[str, Any]:
    approval = _strict_object(
        value,
        required={"status", "reviewer", "reviewed_at", "reason"},
        allowed={"status", "reviewer", "reviewed_at", "reason"},
        label="approval",
    )
    status = approval["status"]
    if status not in APPROVAL_STATES:
        raise ChatV1ContractError("approval.status must be unreviewed, approved, or rejected")
    reviewer = _bounded_text(approval["reviewer"], "approval.reviewer", 80, nullable=True)
    reviewed_at = _canonical_timestamp(approval["reviewed_at"], "approval.reviewed_at", nullable=True)
    reason = _bounded_text(approval["reason"], "approval.reason", 500, nullable=True)
    if status == "unreviewed" and (reviewer is not None or reviewed_at is not None):
        raise ChatV1ContractError("unreviewed records cannot have reviewer or reviewed_at")
    if status in {"approved", "rejected"} and (reviewer is None or reviewed_at is None):
        raise ChatV1ContractError("approved/rejected records require reviewer and reviewed_at")
    if source_type == "real_approved" and status != "approved":
        raise ChatV1ContractError("real_approved source_type requires approval.status=approved")
    result = {"status": status, "reviewer": reviewer, "reviewed_at": reviewed_at, "reason": reason}
    leaks = _leak_errors(result, label="approval")
    if leaks:
        raise ChatV1ContractError("; ".join(leaks))
    return result


def validate_task_snapshot(value: Any) -> dict[str, Any]:
    """Validate the exact immutable V2 prompt used as the only Chat V1 input."""

    snapshot = _strict_object(
        value,
        required={"input", "messages", "input_sha256", "messages_sha256"},
        allowed={"input", "messages", "input_sha256", "messages_sha256"},
        label="task_snapshot",
    )
    try:
        task_input = validate_task_chat_input(snapshot["input"])
    except ContractError as exc:
        raise ChatV1ContractError(f"task_snapshot.input is not exact canonical TASK_CHAT V2: {exc}") from exc
    if canonical_json(task_input) != canonical_json(snapshot["input"]):
        raise ChatV1ContractError("task_snapshot.input is not canonical")
    expected_messages = render_chat_messages(task_input["context"])
    if snapshot["messages"] != expected_messages:
        raise ChatV1ContractError("task_snapshot.messages do not exactly match the canonical TASK_CHAT renderer")
    input_sha256 = snapshot["input_sha256"]
    messages_sha256 = snapshot["messages_sha256"]
    if not isinstance(input_sha256, str) or not _HASH_RE.fullmatch(input_sha256):
        raise ChatV1ContractError("task_snapshot.input_sha256 must be a lowercase SHA-256")
    if not isinstance(messages_sha256, str) or not _HASH_RE.fullmatch(messages_sha256):
        raise ChatV1ContractError("task_snapshot.messages_sha256 must be a lowercase SHA-256")
    actual_input_hash = sha256_json(task_input)
    actual_messages_hash = sha256_json(expected_messages)
    if input_sha256 != actual_input_hash:
        raise ChatV1ContractError("task_snapshot.input_sha256 does not match canonical input")
    if messages_sha256 != actual_messages_hash:
        raise ChatV1ContractError("task_snapshot.messages_sha256 does not match canonical messages")
    return {
        "input": copy.deepcopy(task_input),
        "messages": copy.deepcopy(expected_messages),
        "input_sha256": input_sha256,
        "messages_sha256": messages_sha256,
    }


def _reply_verdict(reply: str, task_input: Mapping[str, Any]) -> dict[str, Any]:
    """Use the shared player-ready gate with the exact V2 context as grounding."""

    try:
        validate_model_safe_text(reply, "approved_reply", MAX_NORMAL_REPLY_CHARACTERS)
    except ContractError as exc:
        return {
            "valid": False,
            "normalized_text": reply,
            "errors": [f"runtime_identifier_or_unsafe_text: {exc}"],
            "warnings": [],
            "character_count": len(reply),
        }
    verdict = dict(
        validate_player_ready_output(
            reply,
            {"input": task_input["context"], "grounding": task_input["context"]},
            apostrophe_policy="reject",
        )
    )
    # The Chat V1 source is byte-stable.  A reviewer may normalize while
    # editing, but a source record must never silently rewrite an approved
    # completion after sign-off.
    if verdict.get("normalized_text") != reply:
        errors = list(verdict.get("errors", []))
        errors.append("approved_reply_requires_exact_straight_apostrophes")
        verdict["errors"] = list(dict.fromkeys(str(error) for error in errors))
        verdict["valid"] = False
    return verdict


def _quality_failure_categories(verdict: Mapping[str, Any]) -> list[str]:
    """Read current and future chat-quality category shapes without guessing."""

    categories: list[str] = []
    for key in ("failure_categories", "style_failure_categories", "assistant_like_categories"):
        value = verdict.get(key)
        if isinstance(value, list):
            categories.extend(str(item) for item in value)
    # Existing and future gates surface their machine-readable reason in
    # errors; keep it in reporting even before a dedicated category is added.
    for value in verdict.get("errors", []) if isinstance(verdict.get("errors"), list) else []:
        text = str(value)
        if any(
            marker in text
            for marker in (
                "generic_assistant",
                "support_agent",
                "self_identifies",
                "denies_normal_persona",
                "unnatural_followup",
                "conversation_reset",
                "excessive_formality",
                "assistant_like",
                "empty_reply_when_reply_expected",
            )
        ):
            categories.append(text)
    return list(dict.fromkeys(categories))


def validate_source_record(record: Mapping[str, Any], *, require_approved: bool = False) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    """Validate one canonical source record without changing it.

    The returned normalized record is suitable for audit persistence, never a
    training row by itself.  ``require_approved`` adds the gates necessary for
    inclusion in a future SFT corpus.
    """

    errors: list[str] = []
    details: dict[str, Any] = {"quality_failure_categories": [], "quality_verdict": None}
    try:
        root = _strict_object(
            record,
            required={
                "record_type",
                "schema_version",
                "record_id",
                "task_snapshot",
                "model_draft",
                "approved_reply",
                "approval",
                "grouping",
                "category",
                "provenance",
                "source_type",
                "variant_group",
                "length_band",
            },
            allowed={
                "record_type",
                "schema_version",
                "record_id",
                "task_snapshot",
                "model_draft",
                "approved_reply",
                "approval",
                "grouping",
                "category",
                "provenance",
                "source_type",
                "variant_group",
                "length_band",
            },
            label="Chat V1 source record",
        )
        if root["record_type"] != SOURCE_RECORD_TYPE:
            raise ChatV1ContractError(f"record_type must be {SOURCE_RECORD_TYPE}")
        if root["schema_version"] != SOURCE_RECORD_VERSION:
            raise ChatV1ContractError(f"schema_version must be {SOURCE_RECORD_VERSION}")
        record_id = _bounded_text(root["record_id"], "record_id", 160)
        if not re.fullmatch(r"chatv1_[0-9a-f]{16,64}", str(record_id)):
            raise ChatV1ContractError("record_id must be a generated opaque chatv1_<hex> handle")
        source_type = root["source_type"]
        if source_type not in SOURCE_TYPES:
            raise ChatV1ContractError(f"source_type must be one of {sorted(SOURCE_TYPES)}")
        task_snapshot = validate_task_snapshot(root["task_snapshot"])
        grouping = validate_grouping(root["grouping"])
        approval = _validate_approval(root["approval"], source_type=source_type)
        category = root["category"]
        if category is not None and category not in CATEGORIES:
            raise ChatV1ContractError(f"category must be null or one of {sorted(CATEGORIES)}")
        variant_group = root["variant_group"]
        if variant_group is not None:
            variant_group = _opaque_group(variant_group, _VARIANT_GROUP_RE, "variant_group")
        provenance = _validate_provenance(root["provenance"])

        draft = root["model_draft"]
        if draft is not None:
            draft = _bounded_text(draft, "model_draft", 4_000)
            draft_leaks = _leak_errors(draft, label="model_draft")
            if draft_leaks:
                raise ChatV1ContractError("; ".join(draft_leaks))
        approved_reply = root["approved_reply"]
        if approved_reply is not None:
            approved_reply = _bounded_text(approved_reply, "approved_reply", MAX_NORMAL_REPLY_CHARACTERS)
        if approval["status"] == "approved":
            if approved_reply is None:
                raise ChatV1ContractError("approved records require approved_reply")
            if category is None:
                raise ChatV1ContractError("approved records require a reviewed category")
        elif approved_reply is not None:
            raise ChatV1ContractError("only approval.status=approved may carry approved_reply")
        length_band = validate_length_band(
            root["length_band"],
            reply=approved_reply,
            required=approval["status"] == "approved",
        )

        normalized = {
            "record_type": SOURCE_RECORD_TYPE,
            "schema_version": SOURCE_RECORD_VERSION,
            "record_id": record_id,
            "task_snapshot": task_snapshot,
            "model_draft": draft,
            "approved_reply": approved_reply,
            "approval": approval,
            "grouping": grouping,
            "category": category,
            "provenance": provenance,
            "source_type": source_type,
            "variant_group": variant_group,
            "length_band": length_band,
        }
        leaks = _leak_errors(normalized, label="source_record")
        if leaks:
            raise ChatV1ContractError("; ".join(leaks))
        if approved_reply is not None:
            verdict = _reply_verdict(approved_reply, task_snapshot["input"])
            details["quality_verdict"] = verdict
            details["quality_failure_categories"] = _quality_failure_categories(verdict)
            if require_approved and not verdict.get("valid"):
                errors.extend(f"approved_reply_quality: {item}" for item in verdict.get("errors", []))
        if require_approved and approval["status"] != "approved":
            errors.append("record is not explicitly human-approved")
        return normalized, list(dict.fromkeys(errors)), details
    except (ChatV1ContractError, ContractError, TypeError, ValueError) as exc:
        errors.append(str(exc))
        return None, list(dict.fromkeys(errors)), details


def training_row_from_source(record: Mapping[str, Any], *, split: str) -> dict[str, Any]:
    """Render an approved source record into the minimal completion-only SFT row."""

    normalized, errors, _details = validate_source_record(record, require_approved=True)
    if normalized is None or errors:
        raise ChatV1ContractError("cannot create training row: " + "; ".join(errors or ["invalid source record"]))
    if split not in {"train", "validation", "test"}:
        raise ChatV1ContractError("split must be train, validation, or test")
    reply = normalized["approved_reply"]
    assert isinstance(reply, str)
    prompt = copy.deepcopy(normalized["task_snapshot"]["messages"])
    completion = [{"role": "assistant", "content": reply}]
    # Metadata remains outside prompt/completion.  The trainer must use only
    # those two fields, so player/thread group aliases never reach a model.
    return {
        "record_id": normalized["record_id"],
        "task": TASK_CHAT,
        "prompt_version": CHAT_PROMPT_VERSION,
        "context_contract_version": CONTEXT_CONTRACT_VERSION,
        "prompt": prompt,
        "completion": completion,
        "messages": prompt + copy.deepcopy(completion),
        "split": split,
        "category": normalized["category"],
        "source_type": normalized["source_type"],
        "length_band": normalized["length_band"],
        "grouping": copy.deepcopy(normalized["grouping"]),
        "variant_group": normalized["variant_group"],
        "task_input_sha256": normalized["task_snapshot"]["input_sha256"],
        "approved_reply_sha256": sha256_text(reply),
    }


def prompt_key(record: Mapping[str, Any]) -> str:
    """Stable exact-prompt key used by duplicate/conflict protection."""

    snapshot = record.get("task_snapshot")
    if not isinstance(snapshot, Mapping):
        return ""
    task_input = snapshot.get("input")
    try:
        return sha256_json(task_input)
    except (ContractError, TypeError, ValueError):
        return ""


def target_key(reply: str) -> str:
    """Normalised comparison key only; presentation text is never rewritten."""

    normalized = " ".join(unicodedata.normalize("NFKC", reply).casefold().split())
    return sha256_text(normalized)


def incoming_message(record: Mapping[str, Any]) -> str | None:
    try:
        context = record["task_snapshot"]["input"]["context"]  # type: ignore[index]
        value = context["incoming_message"]["text"]
        return value if isinstance(value, str) else None
    except (KeyError, TypeError):
        return None


def source_record_from_review_export(
    export_row: Mapping[str, Any],
    *,
    player_group: str,
    thread_group: str,
    category: str,
    length_band: str,
    reviewer: str = "imported_v2_review",
    source_type: str = "real_approved",
) -> dict[str, Any]:
    """Convert one immutable V2 *review* export into an auditable source row.

    Group aliases are deliberately supplied by the operator.  The V2 export
    correctly hides runtime IDs, so this importer refuses to infer groups from
    hidden storage identifiers.
    """

    if export_row.get("record_type") != REVIEW_EXPORT_RECORD_TYPE:
        raise ChatV1ContractError("input is not a PEva V2 approved chat review export")
    snapshot = export_row.get("task_snapshot")
    if not isinstance(snapshot, Mapping):
        raise ChatV1ContractError("review export lacks task_snapshot")
    task = snapshot.get("task")
    if task != TASK_CHAT:
        raise ChatV1ContractError("review export is not a TASK_CHAT record")
    task_input = snapshot.get("input")
    messages = snapshot.get("messages")
    target = export_row.get("approved_target")
    review = export_row.get("review")
    model_draft = export_row.get("model_draft")
    if not isinstance(target, Mapping) or target.get("role") != "assistant" or not isinstance(target.get("content"), str):
        raise ChatV1ContractError("review export lacks an assistant approved_target.content")
    if not isinstance(review, Mapping) or review.get("human_approved") is not True:
        raise ChatV1ContractError("review export has not been explicitly human approved")
    if model_draft is not None and not isinstance(model_draft, str):
        raise ChatV1ContractError("review export model_draft must be a string or null")
    export_id = export_row.get("record_id")
    if not isinstance(export_id, str) or not _HASH_RE.fullmatch(export_id):
        raise ChatV1ContractError("review export record_id must be an opaque SHA-256 handle")
    provenance_reviewed_at = review.get("reviewed_at")
    reviewed_at = provenance_reviewed_at if isinstance(provenance_reviewed_at, str) else utc_now()
    import_identity = canonical_json(
        {
            "export_record_id": export_id,
            "task_input_sha256": snapshot.get("input_sha256"),
            "approved_reply": target.get("content"),
        }
    )
    return {
        "record_type": SOURCE_RECORD_TYPE,
        "schema_version": SOURCE_RECORD_VERSION,
        "record_id": "chatv1_" + sha256_text(import_identity)[:32],
        "task_snapshot": {
            "input": copy.deepcopy(task_input),
            "messages": copy.deepcopy(messages),
            "input_sha256": snapshot.get("input_sha256"),
            "messages_sha256": snapshot.get("messages_sha256"),
        },
        # This field is deliberately independent from approved_reply: an
        # operator may substantially edit a model proposal before approval.
        # It remains review/provenance data and is never rendered into prompt
        # or completion by training_row_from_source.
        "model_draft": copy.deepcopy(model_draft),
        "approved_reply": target.get("content"),
        "approval": {
            "status": "approved",
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "reason": "imported_from_human_approved_v2_capture",
        },
        "grouping": {"player_group": player_group, "thread_group": thread_group},
        "category": category,
        "provenance": {
            "origin": "peva_v2_review_export",
            "source_ref": export_id,
            "captured_at": reviewed_at,
        },
        "source_type": source_type,
        "variant_group": None,
        "length_band": length_band,
    }


def display_context(record: Mapping[str, Any]) -> str:
    """Human-readable exact payload for the review CLI; never model input editing."""

    snapshot = record.get("task_snapshot")
    task_input = snapshot.get("input") if isinstance(snapshot, Mapping) else None
    return json.dumps(task_input, ensure_ascii=False, sort_keys=True, indent=2)

