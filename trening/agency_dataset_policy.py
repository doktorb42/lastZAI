"""Shared, conservative inclusion rules for PEva training datasets.

This module deliberately separates *storage* from *training eligibility*.
Historical data remains available in SQLite; the cleanup manifest tells dataset
tools which historical records must not become positive examples.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from config.runtime_config import DATA_DIR, parse_private_room


CLEANUP_MANIFEST_PATH = DATA_DIR / "cleanup" / "peva_data_cleanup_manifest.json"
PUBLIC_GROUP_ROOMS = frozenset({"custom_lang_en_769"})
TEST_PLAYER_UIDS = frozenset({"p1", "player-1", "player-42"})
POSITIVE_FEEDBACK_RATINGS = frozenset({"GOOD", "EDITED"})

_ACTION_CLAIM = re.compile(
    r"\b(?:reinforced|sent\s+(?:troops|reinforcements)|"
    r"(?:i(?:'ll| will)\s+)?send(?:ing)?\s+(?:troops|reinforcements)|"
    r"joined(?:\s+the\s+rally)?|teleported|attack(?:ed)?\s+(?:him|them|now)|"
    r"(?:i(?:'ll| will)|i can|i(?:'m| am) going to)\s+"
    r"(?:reinforce|join|teleport|attack|send|come))\b",
    re.I,
)
_BARE_ACCEPTANCE = re.compile(
    r"^\s*(?:sure thing|on it|will do|got it|sounds good|okay|ok|"
    r"i can(?: do that)?|i(?:'ll| will) do it)\s*[!.]*\s*$",
    re.I,
)
_BARE_DONE = re.compile(r"^\s*(?:all\s+)?done\s*[!.]*\s*$", re.I)
_TECHNICAL = re.compile(r"(?:^\[|traceback|^\s*\{|exception|error:)", re.I)


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def canonical(value: Any) -> str:
    return clean(value).casefold()


def is_dummy_test_uid(uid: Any) -> bool:
    value = canonical(uid)
    return value in TEST_PLAYER_UIDS or bool(re.fullmatch(r"(?:p|player)[-_]?\d+", value))


def is_unsafe_unconfirmed_action_claim(reply: Any, command_status: Any = None) -> bool:
    text = clean(reply)
    if not text:
        return False
    return bool(_BARE_ACCEPTANCE.fullmatch(text)) or bool(_BARE_DONE.fullmatch(text)) or (
        clean(command_status).upper() != "EXECUTED" and bool(_ACTION_CLAIM.search(text))
    )


def is_technical_text(value: Any) -> bool:
    return bool(_TECHNICAL.search(clean(value)))


def load_cleanup_manifest(path: str | Path = CLEANUP_MANIFEST_PATH) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        return {}
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def manifest_decisions(manifest: Mapping[str, Any] | None = None) -> Iterable[Mapping[str, Any]]:
    value = (manifest or load_cleanup_manifest()).get("decisions", [])
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def classifications_for(
    *,
    room_id: Any = None,
    player_uid: Any = None,
    source_message_key: Any = None,
    manifest: Mapping[str, Any] | None = None,
) -> set[str]:
    """Return conservative external classifications for a historical record."""

    room = clean(room_id)
    uid = clean(player_uid)
    key = clean(source_message_key)
    output: set[str] = set()
    if room in PUBLIC_GROUP_ROOMS:
        output.update({"PUBLIC_OR_GROUP", "REAL_PUBLIC", "EXCLUDE_POSITIVE"})
    if is_dummy_test_uid(uid):
        output.update({"TEST_MEMORY", "EXCLUDE_POSITIVE"})
    for item in manifest_decisions(manifest):
        item_room = clean(item.get("room_id"))
        item_uid = clean(item.get("player_uid"))
        item_key = clean(item.get("source_message_key"))
        match = bool(
            (key and item_key == key)
            or (not key and room and item_room == room and (not uid or not item_uid or item_uid == uid))
            or (not key and not room and uid and item_uid == uid)
        )
        if not match:
            continue
        classification = clean(item.get("classification"))
        if classification:
            output.add(classification)
        usage = item.get("training_usage", [])
        if isinstance(usage, str):
            output.add(usage)
        elif isinstance(usage, list):
            output.update(clean(value) for value in usage if clean(value))
    return output


@dataclass(frozen=True, slots=True)
class InclusionDecision:
    eligible: bool
    reasons: tuple[str, ...]
    classifications: tuple[str, ...]


def positive_real_eligibility(
    record: Mapping[str, Any],
    feedback: Any | None,
    *,
    manifest: Mapping[str, Any] | None = None,
) -> InclusionDecision:
    """Decide whether a real record may enter a positive private dataset.

    A missing rating is intentionally not a positive label.  This remains true
    even when the historical reply appears reasonable.
    """

    reasons: list[str] = []
    room_id = clean(record.get("room_id"))
    uid = clean(record.get("player_uid"))
    key = clean(record.get("message_key") or record.get("source_message_key"))
    message = clean(record.get("input_message") or record.get("text"))
    reply = clean(record.get("reply") or record.get("model_reply"))
    classifications = classifications_for(
        room_id=room_id, player_uid=uid, source_message_key=key, manifest=manifest,
    )

    if clean(record.get("channel") or "custom") != "custom":
        reasons.append("non_private_channel")
    if parse_private_room(room_id) is None:
        reasons.append("not_direct_private_room")
    if "PUBLIC_OR_GROUP" in classifications or "REAL_PUBLIC" in classifications:
        reasons.append("public_or_group_room")
    if is_dummy_test_uid(uid):
        reasons.append("dummy_test_player")
    if {"TEST_COMMAND", "TEST_SAFETY", "TEST_MEMORY", "EXCLUDE_POSITIVE"} & classifications:
        reasons.append("cleanup_classification_excluded")
    if not message or not reply:
        reasons.append("empty")
    if is_technical_text(message) or is_technical_text(reply):
        reasons.append("technical")
    if clean(record.get("reply_rewrite_source")) == "deterministic_fallback":
        reasons.append("automatic_fallback")
    if is_unsafe_unconfirmed_action_claim(reply, record.get("command_status")):
        reasons.append("unsafe_unconfirmed_action_claim")

    rating_value = getattr(feedback, "rating", None) if feedback is not None else None
    if hasattr(rating_value, "value"):
        rating_value = rating_value.value
    rating = clean(rating_value).upper()
    if not rating and isinstance(feedback, Mapping):
        rating = clean(feedback.get("rating")).upper()
    if rating not in POSITIVE_FEEDBACK_RATINGS:
        reasons.append("unreviewed_feedback" if not rating else "non_positive_feedback")
    if rating == "EDITED":
        corrected = clean(
            getattr(feedback, "corrected_reply", None)
            if feedback is not None and not isinstance(feedback, Mapping)
            else (feedback or {}).get("corrected_reply")
        )
        if not corrected:
            reasons.append("edited_without_correction")
        elif is_unsafe_unconfirmed_action_claim(corrected, record.get("command_status")):
            reasons.append("unsafe_edited_reply")
    return InclusionDecision(
        eligible=not reasons,
        reasons=tuple(sorted(set(reasons))),
        classifications=tuple(sorted(classifications)),
    )
