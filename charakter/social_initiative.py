"""Python-owned eligibility and scoring for optional proactive PEva chat.

The manager selects only a player, reason and initiative type.  It does not
generate text, navigate UI, or send anything.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class InitiativeType(str, Enum):
    CHECK_IN = "CHECK_IN"
    FOLLOW_UP = "FOLLOW_UP"
    EVENT_RELATED = "EVENT_RELATED"
    PLAYFUL_PING = "PLAYFUL_PING"
    REMEMBERED_TOPIC = "REMEMBERED_TOPIC"
    RETURNING_PLAYER = "RETURNING_PLAYER"
    GAME_CONTEXT = "GAME_CONTEXT"


@dataclass(frozen=True, slots=True)
class InitiativeCandidate:
    player_uid: str
    room_id: str
    initiative_type: InitiativeType
    reason: str
    score: float
    topic: str | None = None


@dataclass(frozen=True, slots=True)
class InitiativeDecision:
    candidate: InitiativeCandidate | None
    reason: str


class SocialInitiativeManager:
    """Conservative, bounded eligibility with testable randomness."""

    _relationship_base = {
        "UNKNOWN": 0.0, "KNOWN_PLAYER": 0.08, "ALLY": 0.15,
        "FRIEND": 0.28, "OFFICER": 0.12, "LEADER": 0.10,
    }

    def __init__(self, *, enabled: bool = True, global_hourly_limit: int = 3,
                 same_player_min_minutes: int = 180, same_topic_min_minutes: int = 720,
                 rng: random.Random | None = None, now: Any = time.time) -> None:
        self.enabled = bool(enabled)
        self.global_hourly_limit = max(0, min(12, int(global_hourly_limit)))
        self.same_player_seconds = max(30 * 60, min(24 * 3600, int(same_player_min_minutes) * 60))
        self.same_topic_seconds = max(60 * 60, min(48 * 3600, int(same_topic_min_minutes) * 60))
        self.rng = rng or random.Random()
        self.now = now
        self.social_energy = 1.0
        self.recent_reply_count = 0
        self.recent_proactive_count = 0
        self._last_activity_at: float | None = None

    def note_reply(self, *, at: float | None = None) -> None:
        self._note_activity(at=at, proactive=False)

    def note_proactive(self, *, at: float | None = None) -> None:
        self._note_activity(at=at, proactive=True)

    def _note_activity(self, *, at: float | None, proactive: bool) -> None:
        current = float(self.now() if at is None else at)
        self._recover_energy(current)
        self.social_energy = max(0.15, self.social_energy - (0.12 if proactive else 0.05))
        self.recent_reply_count += 0 if proactive else 1
        self.recent_proactive_count += 1 if proactive else 0
        self._last_activity_at = current

    def _recover_energy(self, current: float) -> None:
        if self._last_activity_at is None:
            return
        quiet_hours = max(0.0, current - self._last_activity_at) / 3600.0
        if quiet_hours:
            self.social_energy = min(1.0, self.social_energy + quiet_hours * 0.08)

    def choose(self, profiles: Iterable[dict[str, Any]], *, proactive_last_hour: int,
               send_lane_busy: bool = False, ambiguous_delivery: bool = False,
               navigation_conflict: bool = False, at: float | None = None) -> InitiativeDecision:
        current = float(self.now() if at is None else at)
        self._recover_energy(current)
        if not self.enabled:
            return InitiativeDecision(None, "disabled")
        if send_lane_busy:
            return InitiativeDecision(None, "send_lane_busy")
        if ambiguous_delivery:
            return InitiativeDecision(None, "ambiguous_delivery")
        if navigation_conflict:
            return InitiativeDecision(None, "navigation_conflict")
        if proactive_last_hour >= self.global_hourly_limit:
            return InitiativeDecision(None, "global_hourly_limit")
        best: InitiativeCandidate | None = None
        skip_reason = "no_eligible_player"
        for item in profiles:
            candidate, reason = self._evaluate_player(item, current)
            if candidate is not None and (best is None or candidate.score > best.score):
                best = candidate
            elif reason:
                skip_reason = reason
        if best is None:
            return InitiativeDecision(None, skip_reason)
        # Score is deliberately also the probability gate. A deterministic
        # Random instance can be injected by offline tests.
        if self.rng.random() > best.score:
            return InitiativeDecision(None, "probability_gate")
        return InitiativeDecision(best, "eligible")

    def _evaluate_player(self, item: dict[str, Any], now: float) -> tuple[InitiativeCandidate | None, str]:
        uid = str(item.get("player_uid") or "")
        room_id = str(item.get("last_room_id") or "")
        relationship = str(item.get("relationship") or "UNKNOWN").upper()
        familiarity = _bounded(item.get("familiarity"), 0.0)
        if not uid or not room_id:
            return None, "missing_private_context"
        if relationship == "UNKNOWN":
            return None, "unknown_player"
        if bool(item.get("proactive_suppressed_indefinitely")):
            return None, "explicit_opt_out"
        suppressed_until = _number(item.get("proactive_suppressed_until"))
        if suppressed_until is not None and suppressed_until > now:
            return None, "suppressed"
        last = _number(item.get("last_proactive_at"))
        if last is not None and now - last < self.same_player_seconds:
            return None, "cooldown"
        if last is not None and str(item.get("last_proactive_topic") or "") and now - last < self.same_topic_seconds:
            return None, "same_topic_cooldown"
        last_incoming = _number(item.get("last_incoming_at"))
        if last_incoming is None:
            return None, "no_incoming_history"
        interest = _bounded(item.get("conversation_interest_score"), 0.0)
        elapsed_hours = min(72.0, max(0.0, (now - last_incoming) / 3600.0))
        base_score = self._relationship_base.get(relationship, 0.0) + familiarity * 0.22 + interest * 0.10 + min(0.05, elapsed_hours / 1440.0)
        activity_penalty = min(0.18, self.recent_reply_count * 0.01 + self.recent_proactive_count * 0.03)
        score = min(0.45, max(0.0, base_score - activity_penalty) * self.social_energy)
        if score <= 0.0:
            return None, "relationship_or_familiarity_too_low"
        topic = str(item.get("last_proactive_topic") or "") or None
        # Without a structured open follow-up, return a contextual check-in;
        # text still comes from the model and is subject to all gates.
        kind = InitiativeType.FOLLOW_UP if interest >= 0.45 else InitiativeType.RETURNING_PLAYER
        reason = "open_conversation_interest" if kind == InitiativeType.FOLLOW_UP else "returning_known_player"
        return InitiativeCandidate(uid, room_id, kind, reason, score, topic), ""


def negative_social_signal(text: str) -> tuple[bool, bool]:
    """Return (suppresses, indefinite) without treating short replies as rejection."""
    value = " ".join(str(text or "").casefold().split())
    strong = ("leave me alone", "don't message me", "do not message me", "stop messaging", "never message me")
    soft = ("busy", "not now", "later", "i'm done", "im done", "leave it")
    if any(item in value for item in strong):
        return True, True
    return any(item in value for item in soft), False


def _number(value: Any) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None


def _bounded(value: Any, default: float) -> float:
    number = _number(value)
    return min(1.0, max(0.0, number if number is not None else default))


def main() -> None:
    """Offline eligibility inspection; this command cannot generate or send."""
    import argparse
    from pathlib import Path
    from charakter.humanization_store import HumanizationStore
    from config.runtime_config import DATA_DIR

    parser = argparse.ArgumentParser(description="PEva proactive initiative dry run")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate eligibility only; never generate or send.")
    parser.add_argument("--db", default=str(DATA_DIR / "databases" / "humanization.db"))
    args = parser.parse_args()
    if not args.dry_run:
        parser.error("Only --dry-run is supported; proactive sending is a PlayerLoop feature.")
    with HumanizationStore(Path(args.db)) as store:
        now = time.time()
        manager = SocialInitiativeManager(now=lambda: now)
        decision = manager.choose(
            store.list_social_candidates(),
            proactive_last_hour=store.proactive_count_since(now - 3600),
            at=now,
        )
    if decision.candidate is None:
        print(f"[PROACTIVE SKIP] reason={decision.reason}")
    else:
        item = decision.candidate
        print(
            f"[PROACTIVE CHECK] uid={item.player_uid} type={item.initiative_type.value} "
            f"score={item.score:.2f} reason={item.reason}"
        )


if __name__ == "__main__":
    main()
