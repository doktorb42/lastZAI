"""Export immutable approved PEva V2 review records.

This module is deliberately *not* a dataset builder or trainer.  It only turns
fully reviewed, delivered captures into auditable review records while keeping
the exact V2 task inputs/messages persisted at request time.  A separate future
redaction, quality-review, deduplication, and split step must decide whether any
record can become training data.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import (
    CHAT_PROMPT_VERSION,
    CONTEXT_CONTRACT_VERSION,
    MEMORY_PROMPT_VERSION,
    TASK_CHAT,
    TASK_MEMORY,
    ContractError,
    canonical_json,
    sha256_text,
    utc_now,
    validate_task_chat_input,
    validate_task_memory_input,
)
from .database import ConversationRepository


REVIEW_EXPORT_FORMAT_VERSION = "peva-v2-approved-capture-review-record-v2"


class ExportError(RuntimeError):
    """A requested export is not a safe immutable V2 review capture."""


def _write_jsonl_new(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists():
        raise ExportError(f"refusing to overwrite existing review export: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise ExportError(f"refusing to overwrite existing review manifest: {path}")
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_copy(value: Any, label: str) -> Any:
    """Copy through canonical JSON so export cannot mutate a repository row."""

    try:
        return json.loads(canonical_json(value))
    except (ContractError, json.JSONDecodeError) as exc:
        raise ExportError(f"{label} is not canonical JSON") from exc


def _mapping(source: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = source.get(field)
    if not isinstance(value, Mapping):
        raise ExportError(f"eligible capture is missing object field {field}")
    return _json_copy(dict(value), field)


def _messages(source: Mapping[str, Any], field: str) -> list[dict[str, str]]:
    value = source.get(field)
    if not isinstance(value, list) or not value:
        raise ExportError(f"eligible capture is missing prompt messages {field}")
    copied = _json_copy(value, field)
    if not isinstance(copied, list) or any(
        not isinstance(item, dict)
        or set(item) != {"role", "content"}
        or not isinstance(item["role"], str)
        or not isinstance(item["content"], str)
        for item in copied
    ):
        raise ExportError(f"{field} is not a list of exact chat messages")
    return copied


def _verify_hash(source: Mapping[str, Any], field: str, serialized: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or len(value) != 64:
        raise ExportError(f"eligible capture is missing SHA-256 field {field}")
    if value != sha256_text(serialized):
        raise ExportError(f"immutable capture hash mismatch for {field}")
    return value


def _payload_from_messages(messages: list[dict[str, str]], task: str) -> dict[str, Any]:
    """Read the task payload from the persisted user message without rebuilding it."""

    if len(messages) != 2 or messages[0]["role"] != "system" or messages[1]["role"] != "user":
        raise ExportError(f"persisted {task} prompt must contain exactly system and user messages")
    _prefix, separator, encoded = messages[1]["content"].partition("\n")
    if not separator or not encoded:
        raise ExportError(f"persisted {task} user message has no JSON payload")
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ExportError(f"persisted {task} user message is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ExportError(f"persisted {task} payload is not an object")
    return payload


def _verify_task_snapshot(
    *,
    source: Mapping[str, Any],
    task: str,
    input_field: str,
    input_hash_field: str,
    messages_field: str,
    messages_hash_field: str,
    context: Mapping[str, Any],
    validator: Callable[[Mapping[str, Any]], dict[str, Any]],
    expected_prompt_version: str,
) -> tuple[dict[str, Any], list[dict[str, str]], str, str]:
    """Validate an immutable stored task snapshot, never reconstruct one."""

    task_input = _mapping(source, input_field)
    messages = _messages(source, messages_field)
    try:
        validated = validator(task_input)
    except ContractError as exc:
        raise ExportError(f"stored {task} input no longer satisfies the V2 contract: {exc}") from exc
    if canonical_json(validated) != canonical_json(task_input):
        raise ExportError(f"stored {task} input is not canonical and cannot be exported unchanged")
    if task_input.get("task") != task or task_input.get("prompt_version") != expected_prompt_version:
        raise ExportError(f"stored task snapshot has the wrong {task} tag or prompt version")
    if task_input.get("context_contract_version") != CONTEXT_CONTRACT_VERSION:
        raise ExportError("stored task snapshot has an unexpected context contract version")
    if canonical_json(task_input.get("context")) != canonical_json(context):
        raise ExportError(f"stored {task} task input does not use the immutable context snapshot")
    message_payload = _payload_from_messages(messages, task)
    if canonical_json(message_payload) != canonical_json(task_input):
        raise ExportError(f"stored {task} rendered prompt differs from its immutable task input")
    input_hash = _verify_hash(source, input_hash_field, canonical_json(task_input))
    messages_hash = _verify_hash(source, messages_hash_field, canonical_json(messages))
    return task_input, messages, input_hash, messages_hash


def _review_record_id(*, task: str, context_sha256: str, task_input_sha256: str, messages_sha256: str, target: Any) -> str:
    """Stable audit handle that does not expose a turn/player/database identifier."""

    return sha256_text(
        canonical_json(
            {
                "format": REVIEW_EXPORT_FORMAT_VERSION,
                "task": task,
                "context_sha256": context_sha256,
                "task_input_sha256": task_input_sha256,
                "messages_sha256": messages_sha256,
                "target": target,
            }
        )
    )


def _review_metadata(source: Mapping[str, Any]) -> dict[str, Any]:
    """Keep approval provenance without carrying storage/routing IDs into records."""

    reviewer = source.get("reviewer")
    reviewed_at = source.get("reviewed_at")
    return {
        "human_approved": True,
        "reviewer": reviewer if isinstance(reviewer, str) else None,
        "reviewed_at": reviewed_at if isinstance(reviewed_at, str) else None,
        "delivery_confirmed": True,
        "memory_update_applied": True,
    }


def _model_draft(source: Mapping[str, Any]) -> str | None:
    """Export the original visible model draft separately from the approval.

    It is review metadata, never a model target.  Keeping the raw draft lets
    Chat V1 review show whether an operator edited a response before approval;
    the downstream source-record contract independently leak-scans it before
    it can be retained in a training candidate.
    """

    value = source.get("generated_reply_raw")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExportError("eligible capture has a non-text generated_reply_raw")
    if len(value) > 4_000:
        raise ExportError("eligible capture generated_reply_raw exceeds the Chat V1 review limit")
    return value


def export_approved_examples(repository: ConversationRepository, output_directory: Path) -> dict[str, Any]:
    """Write V2 review records from fully reviewed immutable captures.

    The name is preserved for the pilot CLI/API.  The output explicitly calls
    itself *review records*, not a dataset, and this function is only invoked
    on an operator's explicit request.
    """

    sources = repository.exportable_turns()
    if not sources:
        raise ExportError("no fully approved, applied-memory, delivered, untruncated V2 captures are eligible for review export")

    output_directory = output_directory.resolve()
    timestamp = utc_now().replace(":", "").replace("-", "")
    chat_path = output_directory / f"peva_v2_approved_chat_review_records_{timestamp}.jsonl"
    memory_path = output_directory / f"peva_v2_approved_memory_review_records_{timestamp}.jsonl"
    manifest_path = output_directory / f"peva_v2_approved_capture_review_manifest_{timestamp}.json"

    chat_rows: list[dict[str, Any]] = []
    memory_rows: list[dict[str, Any]] = []
    for source in sources:
        if bool(source.get("context_truncated")):
            raise ExportError("repository returned a truncated context as exportable")
        context = _mapping(source, "context_json")
        try:
            # Validation happens through both task inputs below.  It is not
            # enough to trust a row just because it was once marked eligible.
            context_serialized = canonical_json(context)
        except ContractError as exc:
            raise ExportError("stored context is not canonical JSON") from exc
        context_sha256 = _verify_hash(source, "context_sha256", context_serialized)

        chat_input, chat_messages, chat_input_sha256, chat_messages_sha256 = _verify_task_snapshot(
            source=source,
            task=TASK_CHAT,
            input_field="chat_input_json",
            input_hash_field="chat_input_sha256",
            messages_field="chat_snapshot_messages_json",
            messages_hash_field="chat_messages_sha256",
            context=context,
            validator=validate_task_chat_input,
            expected_prompt_version=CHAT_PROMPT_VERSION,
        )
        memory_input, memory_messages, memory_input_sha256, memory_messages_sha256 = _verify_task_snapshot(
            source=source,
            task=TASK_MEMORY,
            input_field="memory_input_json",
            input_hash_field="memory_input_sha256",
            messages_field="memory_snapshot_messages_json",
            messages_hash_field="memory_messages_sha256",
            context=context,
            validator=validate_task_memory_input,
            expected_prompt_version=MEMORY_PROMPT_VERSION,
        )

        approved_reply = source.get("approved_reply")
        if not isinstance(approved_reply, str) or approved_reply != memory_input.get("approved_reply"):
            raise ExportError("approved reply is absent or differs from the immutable TASK_MEMORY input")
        memory_update = _mapping(source, "parsed_json")
        if memory_update.get("schema_version") != "peva-memory-update-v2":
            raise ExportError("applied memory output is not a peva-memory-update-v2 proposal")

        review = _review_metadata(source)
        model_draft = _model_draft(source)
        chat_target = {"role": "assistant", "content": approved_reply}
        memory_target = {"role": "assistant", "content": canonical_json(memory_update)}
        shared_snapshot = {
            "context_contract_version": CONTEXT_CONTRACT_VERSION,
            "context_sha256": context_sha256,
            "context": context,
        }
        chat_rows.append(
            {
                "record_type": "peva_v2_approved_chat_review_record",
                "export_format_version": REVIEW_EXPORT_FORMAT_VERSION,
                "training_status": "review_only_not_a_training_dataset",
                "record_id": _review_record_id(
                    task=TASK_CHAT,
                    context_sha256=context_sha256,
                    task_input_sha256=chat_input_sha256,
                    messages_sha256=chat_messages_sha256,
                    target=chat_target,
                ),
                "immutable_snapshot": shared_snapshot,
                "task_snapshot": {
                    "task": TASK_CHAT,
                    "prompt_version": CHAT_PROMPT_VERSION,
                    "input_sha256": chat_input_sha256,
                    "messages_sha256": chat_messages_sha256,
                    "input": chat_input,
                    "messages": chat_messages,
                },
                "model_draft": model_draft,
                "approved_target": chat_target,
                "review": review,
            }
        )
        memory_rows.append(
            {
                "record_type": "peva_v2_approved_memory_review_record",
                "export_format_version": REVIEW_EXPORT_FORMAT_VERSION,
                "training_status": "review_only_not_a_training_dataset",
                "record_id": _review_record_id(
                    task=TASK_MEMORY,
                    context_sha256=context_sha256,
                    task_input_sha256=memory_input_sha256,
                    messages_sha256=memory_messages_sha256,
                    target=memory_target,
                ),
                "immutable_snapshot": shared_snapshot,
                "task_snapshot": {
                    "task": TASK_MEMORY,
                    "prompt_version": MEMORY_PROMPT_VERSION,
                    "input_sha256": memory_input_sha256,
                    "messages_sha256": memory_messages_sha256,
                    "input": memory_input,
                    "messages": memory_messages,
                },
                "approved_target": memory_target,
                "review": review,
            }
        )

    _write_jsonl_new(chat_path, chat_rows)
    _write_jsonl_new(memory_path, memory_rows)
    manifest = {
        "export_format_version": REVIEW_EXPORT_FORMAT_VERSION,
        "status": "exported_for_future_human_review_only_not_training_data",
        "created_at": utc_now(),
        "chat_review_records": len(chat_rows),
        "memory_review_records": len(memory_rows),
        "chat_review_records_path": str(chat_path),
        "memory_review_records_path": str(memory_path),
        "chat_sha256": _sha256_file(chat_path),
        "memory_sha256": _sha256_file(memory_path),
        "guarantees": [
            "exact immutable peva-conversation-context-v2 snapshot retained",
            "original model draft is retained separately from the human-approved target",
            "exact persisted V2 task input and rendered prompt retained",
            "only human-approved and externally delivered replies",
            "only Python-validated and explicitly applied memory updates",
            "no raw player UID room ID routing identifier or SQLite UUID in model-facing task records",
            "no training started and no dataset path populated",
            "no game or ADB interaction",
        ],
        "required_before_any_training": [
            "human redaction and quality review",
            "deduplication",
            "scenario-family split",
            "player/conversation-disjoint holdout",
        ],
    }
    _write_json_new(manifest_path, manifest)
    return manifest
