"""Local, human-reviewed friendship proposals for PEva.

The store deliberately contains relationship metadata only.  It never receives
or persists chat text, controls Last Z, sends a message, or accepts a friend
request.  The local panel records actions the human has already taken in the
game and uses a confirmed alias as the durable identity key.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from oczy.identity import ConversationHeader


DEFAULT_STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "runtime" / "friendships.json"
MIN_USER_TURNS = 6
MIN_ASSISTANT_TURNS = 4
DEFER_DAYS = 30
_ALIAS_CHARS = re.compile(r"[^A-Za-z0-9 _.-]+")


class FriendshipStatus(str, Enum):
    """Stages that require a human action outside PEva."""

    REVIEW = "REVIEW"
    OFFER_SENT = "OFFER_SENT"
    NEEDS_ALIAS = "NEEDS_ALIAS"
    FRIEND = "FRIEND"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class FriendCandidate:
    """A candidate record with no incoming chat content."""

    candidate_id: str
    source_key: str
    display_name: str
    alliance: str | None
    proposed_alias: str
    status: FriendshipStatus
    created_at_ms: int
    updated_at_ms: int
    next_eligible_at_ms: int = 0
    final_alias: str | None = None
    auto_reply_approved: bool = False
    proposal_count: int = 1

    @property
    def draft_message(self) -> str:
        """Fixed social draft; never sent by PEva or the panel."""
        return "Hey, would you like to be friends?"

    def public_dict(self) -> dict[str, object]:
        """Local-panel representation containing metadata and PEva's draft."""
        return {
            "id": self.candidate_id,
            "display_name": self.display_name,
            "alliance": self.alliance,
            "proposed_alias": self.proposed_alias,
            "final_alias": self.final_alias,
            "auto_reply_approved": self.auto_reply_approved,
            "status": self.status.value,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "next_eligible_at_ms": self.next_eligible_at_ms,
            "proposal_count": self.proposal_count,
            "draft_message": self.draft_message,
        }


class FriendshipStore:
    """Thread-safe local metadata store for a human-reviewed friend workflow."""

    def __init__(self, path: Path | str = DEFAULT_STORE_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._candidates = self._load()

    def observe_conversation(
        self,
        header: ConversationHeader,
        *,
        user_turns: int,
        assistant_turns: int,
        now_ms: int | None = None,
    ) -> FriendCandidate | None:
        """Create/reopen a proposal only after a clearly mutual conversation.

        Counts are supplied by the RAM-only conversation context.  No message
        text enters this function or its durable file.
        """
        if user_turns < MIN_USER_TURNS or assistant_turns < MIN_ASSISTANT_TURNS:
            return None
        # A plain centred OCR title is sufficient for one temporary chat
        # session, but not for a durable social proposal.  It could be a
        # generic screen title or a nickname that later changes.
        if header.source == "private_dedicated_header_ocr":
            return None
        now = _now(now_ms)
        source_key = _source_key(header)
        with self._lock:
            if header.source == "private_alias_ocr":
                alias_match = self._by_final_alias(normalize_alias(header.display_name))
                if alias_match is not None:
                    return alias_match
            existing = self._by_source_key(source_key)
            if existing is not None:
                if existing.status in {
                    FriendshipStatus.REVIEW,
                    FriendshipStatus.OFFER_SENT,
                    FriendshipStatus.NEEDS_ALIAS,
                    FriendshipStatus.FRIEND,
                    FriendshipStatus.BLOCKED,
                }:
                    return existing
                if existing.status is FriendshipStatus.DEFERRED and now < existing.next_eligible_at_ms:
                    return existing
                # A deferred candidate may return after the cooldown only when
                # another mutual conversation is observed.
                reopened = _replace_candidate(
                    existing,
                    status=FriendshipStatus.REVIEW,
                    updated_at_ms=now,
                    next_eligible_at_ms=0,
                    proposal_count=existing.proposal_count + 1,
                )
                self._replace(reopened)
                self._save()
                return reopened

            candidate = FriendCandidate(
                candidate_id=uuid.uuid4().hex,
                source_key=source_key,
                display_name=header.display_name,
                alliance=header.alliance,
                proposed_alias=_proposed_alias(header.display_name),
                status=FriendshipStatus.REVIEW,
                created_at_ms=now,
                updated_at_ms=now,
            )
            self._candidates.append(candidate)
            self._save()
            return candidate

    def register_confirmed_friend(
        self,
        alias: str,
        *,
        now_ms: int | None = None,
    ) -> FriendCandidate:
        """Register an existing friend after the user set its alias in Last Z.

        The user has already checked the profile and set a unique alias on
        PEva's account. This local record never contacts the game and is the
        safe bootstrap for an existing friend.
        """
        final_alias = normalize_alias(alias)
        now = _now(now_ms)
        with self._lock:
            if self._by_final_alias(final_alias) is not None:
                raise ValueError("This alias already belongs to a PEva friend.")
            candidate = FriendCandidate(
                candidate_id=uuid.uuid4().hex,
                source_key=f"manual|{final_alias.casefold()}",
                display_name=final_alias,
                alliance=None,
                proposed_alias=final_alias,
                status=FriendshipStatus.FRIEND,
                created_at_ms=now,
                updated_at_ms=now,
                final_alias=final_alias,
            )
            self._candidates.append(candidate)
            self._save()
            return candidate

    def mark_offer_sent(self, candidate_id: str, *, now_ms: int | None = None) -> FriendCandidate:
        """Record that the human manually sent PEva's visible draft."""
        return self._transition(
            candidate_id,
            allowed={FriendshipStatus.REVIEW},
            status=FriendshipStatus.OFFER_SENT,
            now_ms=now_ms,
        )

    def mark_friend_added(self, candidate_id: str, *, now_ms: int | None = None) -> FriendCandidate:
        """Record that the human confirmed a friendship in Last Z."""
        return self._transition(
            candidate_id,
            allowed={FriendshipStatus.OFFER_SENT},
            status=FriendshipStatus.NEEDS_ALIAS,
            now_ms=now_ms,
        )

    def confirm_alias(
        self,
        candidate_id: str,
        alias: str,
        *,
        now_ms: int | None = None,
    ) -> FriendCandidate:
        """Confirm the alias the human already set manually in Last Z."""
        normalized = normalize_alias(alias)
        with self._lock:
            candidate = self._require(candidate_id)
            if candidate.status is not FriendshipStatus.NEEDS_ALIAS:
                raise ValueError("Alias can be confirmed only after friendship is marked added.")
            other = self._by_final_alias(normalized)
            if other is not None and other.candidate_id != candidate.candidate_id:
                raise ValueError("This alias already belongs to another PEva friend.")
            confirmed = _replace_candidate(
                candidate,
                status=FriendshipStatus.FRIEND,
                final_alias=normalized,
                updated_at_ms=_now(now_ms),
            )
            self._replace(confirmed)
            self._save()
            return confirmed

    def defer(self, candidate_id: str, *, now_ms: int | None = None) -> FriendCandidate:
        """Hide an unaccepted proposal for a fixed, non-spam cooldown."""
        now = _now(now_ms)
        return self._transition(
            candidate_id,
            allowed={FriendshipStatus.REVIEW},
            status=FriendshipStatus.DEFERRED,
            now_ms=now,
            next_eligible_at_ms=now + DEFER_DAYS * 24 * 60 * 60 * 1000,
        )

    def block(self, candidate_id: str, *, now_ms: int | None = None) -> FriendCandidate:
        """Permanently suppress a person until a future explicit unblocking feature."""
        return self._transition(
            candidate_id,
            allowed={
                FriendshipStatus.REVIEW,
                FriendshipStatus.OFFER_SENT,
                FriendshipStatus.NEEDS_ALIAS,
                FriendshipStatus.DEFERRED,
            },
            status=FriendshipStatus.BLOCKED,
            now_ms=now_ms,
        )

    def remove_friend(self, candidate_id: str) -> FriendCandidate:
        """Remove a confirmed friend from PEva's local list only.

        This cannot remove the player from Last Z, change their alias or send
        anything.  It simply revokes PEva's local recognition and any future
        reply approval for this record.
        """
        with self._lock:
            candidate = self._require(candidate_id)
            if candidate.status is not FriendshipStatus.FRIEND:
                raise ValueError("Only a confirmed PEva friend can be removed from this list.")
            self._candidates = [item for item in self._candidates if item.candidate_id != candidate_id]
            self._save()
            return candidate

    def confirmed_aliases(self) -> tuple[str, ...]:
        """Return only aliases explicitly confirmed by the human."""
        with self._lock:
            return tuple(
                item.final_alias
                for item in self._candidates
                if item.status is FriendshipStatus.FRIEND and item.final_alias
            )

    def set_auto_reply_approval(
        self,
        candidate_id: str,
        approved: bool,
        *,
        now_ms: int | None = None,
    ) -> FriendCandidate:
        """Record the user's local consent for a future reply lane only."""
        with self._lock:
            candidate = self._require(candidate_id)
            if candidate.status is not FriendshipStatus.FRIEND or not candidate.final_alias:
                raise ValueError("Automatic replies can be reviewed only for a confirmed friend alias.")
            updated = _replace_candidate(
                candidate,
                auto_reply_approved=bool(approved),
                updated_at_ms=_now(now_ms),
            )
            self._replace(updated)
            self._save()
            return updated

    def approved_auto_reply_aliases(self) -> tuple[str, ...]:
        """Return only manually confirmed aliases with explicit user consent."""
        with self._lock:
            return tuple(
                item.final_alias
                for item in self._candidates
                if item.status is FriendshipStatus.FRIEND
                and item.final_alias
                and item.auto_reply_approved
            )

    def snapshot(self) -> tuple[dict[str, object], ...]:
        """Return local-panel data ordered by latest change, without chat text."""
        with self._lock:
            ordered = sorted(self._candidates, key=lambda item: item.updated_at_ms, reverse=True)
            return tuple(item.public_dict() for item in ordered)

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            counts = {status.value: 0 for status in FriendshipStatus}
            for item in self._candidates:
                counts[item.status.value] += 1
            return counts

    def _transition(
        self,
        candidate_id: str,
        *,
        allowed: set[FriendshipStatus],
        status: FriendshipStatus,
        now_ms: int | None,
        next_eligible_at_ms: int = 0,
    ) -> FriendCandidate:
        with self._lock:
            candidate = self._require(candidate_id)
            if candidate.status not in allowed:
                raise ValueError(f"Cannot change {candidate.status.value} to {status.value}.")
            updated = _replace_candidate(
                candidate,
                status=status,
                updated_at_ms=_now(now_ms),
                next_eligible_at_ms=next_eligible_at_ms,
            )
            self._replace(updated)
            self._save()
            return updated

    def _require(self, candidate_id: str) -> FriendCandidate:
        for item in self._candidates:
            if item.candidate_id == candidate_id:
                return item
        raise ValueError("Unknown friend candidate.")

    def _by_source_key(self, source_key: str) -> FriendCandidate | None:
        return next((item for item in self._candidates if item.source_key == source_key), None)

    def _by_final_alias(self, alias: str) -> FriendCandidate | None:
        return next((item for item in self._candidates if item.final_alias == alias), None)

    def _replace(self, updated: FriendCandidate) -> None:
        self._candidates = [
            updated if item.candidate_id == updated.candidate_id else item
            for item in self._candidates
        ]

    def _load(self) -> list[FriendCandidate]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            records = raw.get("candidates", []) if isinstance(raw, dict) else []
        except (OSError, ValueError, TypeError):
            return []
        loaded: list[FriendCandidate] = []
        for value in records:
            try:
                if not isinstance(value, dict):
                    continue
                loaded.append(FriendCandidate(
                    candidate_id=_required_text(value.get("candidate_id")),
                    source_key=_required_text(value.get("source_key")),
                    display_name=_required_text(value.get("display_name")),
                    alliance=_optional_text(value.get("alliance")),
                    proposed_alias=normalize_alias(_required_text(value.get("proposed_alias"))),
                    status=FriendshipStatus(_required_text(value.get("status"))),
                    created_at_ms=_positive_int(value.get("created_at_ms")),
                    updated_at_ms=_positive_int(value.get("updated_at_ms")),
                    next_eligible_at_ms=_nonnegative_int(value.get("next_eligible_at_ms", 0)),
                    final_alias=(
                        normalize_alias(value["final_alias"])
                        if isinstance(value.get("final_alias"), str) and value["final_alias"].strip()
                        else None
                    ),
                    auto_reply_approved=bool(value.get("auto_reply_approved", False)),
                    proposal_count=max(1, _positive_int(value.get("proposal_count", 1))),
                ))
            except (TypeError, ValueError):
                continue
        return loaded

    def _save(self) -> None:
        payload = {
            "version": 2,
            "candidates": [
                {**asdict(item), "status": item.status.value}
                for item in self._candidates
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def normalize_alias(value: str) -> str:
    """Normalize a human-entered local alias without guessing an identity."""
    compact = " ".join(_ALIAS_CHARS.sub("", str(value or "")).split()).upper()
    if not 2 <= len(compact) <= 32 or not re.search(r"[A-Z0-9]", compact):
        raise ValueError("Alias must have 2–32 letters, digits, spaces, dots, hyphens, or underscores.")
    return compact


def _source_key(header: ConversationHeader) -> str:
    return f"{header.alliance or ''}|{header.display_name.casefold()}"


def _proposed_alias(display_name: str) -> str:
    core = _ALIAS_CHARS.sub("_", display_name.upper()).replace(" ", "_").strip(" _.-") or "FRIEND"
    return normalize_alias(f"CAND_{core[:23]}")


def _replace_candidate(candidate: FriendCandidate, **changes: object) -> FriendCandidate:
    values = {
        "candidate_id": candidate.candidate_id,
        "source_key": candidate.source_key,
        "display_name": candidate.display_name,
        "alliance": candidate.alliance,
        "proposed_alias": candidate.proposed_alias,
        "status": candidate.status,
        "created_at_ms": candidate.created_at_ms,
        "updated_at_ms": candidate.updated_at_ms,
        "next_eligible_at_ms": candidate.next_eligible_at_ms,
        "final_alias": candidate.final_alias,
        "auto_reply_approved": candidate.auto_reply_approved,
        "proposal_count": candidate.proposal_count,
    }
    values.update(changes)
    return FriendCandidate(**values)  # type: ignore[arg-type]


def _now(value: int | None) -> int:
    return int(time.time() * 1000) if value is None else int(value)


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text is required")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError("positive integer is required")
    return value


def _nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError("non-negative integer is required")
    return value
