"""Priority goals with deterministic lifecycle and PM deduplication."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Mapping


class GoalType(str, Enum):
    ANSWER_PRIVATE_MESSAGE = "ANSWER_PRIVATE_MESSAGE"
    PROACTIVE_PRIVATE_MESSAGE = "PROACTIVE_PRIVATE_MESSAGE"
    ANSWER_ALLIANCE_MESSAGE = "ANSWER_ALLIANCE_MESSAGE"
    CHECK_PRIVATE_CHAT = "CHECK_PRIVATE_CHAT"
    CHECK_ALLIANCE_CHAT = "CHECK_ALLIANCE_CHAT"
    RETURN_TO_SAFE_STATE = "RETURN_TO_SAFE_STATE"
    WAIT = "WAIT"
    ASK_USER = "ASK_USER"


class GoalStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class GoalPriority(IntEnum):
    CRITICAL = 100
    HIGH = 75
    NORMAL = 50
    LOW = 25
    BACKGROUND = 0


@dataclass(slots=True)
class Goal:
    goal_type: GoalType
    priority: GoalPriority = GoalPriority.NORMAL
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    expires_at: float | None = None
    not_before: float | None = None
    max_attempts: int = 3
    goal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    status: GoalStatus = GoalStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    waiting_reason: str | None = None
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_type": self.goal_type.value,
            "priority": int(self.priority),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "not_before": self.not_before,
            "status": self.status.value,
            "reason": self.reason,
            "payload": self.payload,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "waiting_reason": self.waiting_reason,
            "blocked_reason": self.blocked_reason,
        }

    def is_expired(self, now: float | None = None) -> bool:
        return self.expires_at is not None and (now or time.time()) >= self.expires_at

    def is_ready(self, now: float | None = None) -> bool:
        return self.not_before is None or (now or time.time()) >= self.not_before


class GoalManager:
    def __init__(self) -> None:
        self._goals: dict[str, Goal] = {}

    def add_goal(self, goal: Goal) -> Goal:
        """Add a goal or merge it into a compatible active/pending goal."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal")

        existing = self._find_duplicate(goal)
        if existing is None:
            self._goals[goal.goal_id] = goal
            return goal

        existing.priority = max(existing.priority, goal.priority)
        existing.expires_at = _latest_expiry(existing.expires_at, goal.expires_at)
        existing.not_before = _latest_not_before(existing.not_before, goal.not_before)
        existing.reason = goal.reason or existing.reason
        _merge_payload(existing.payload, goal.payload)
        return existing

    def get_goal(self, goal_id: str | None) -> Goal | None:
        return self._goals.get(str(goal_id)) if goal_id else None

    def get_next_goal(self, *, now: float | None = None) -> Goal | None:
        current = time.time() if now is None else float(now)
        for goal in self._goals.values():
            if goal.status in {GoalStatus.PENDING, GoalStatus.ACTIVE} and goal.is_expired(current):
                goal.status = GoalStatus.FAILED
                goal.last_error = "expired"

        active = [goal for goal in self._goals.values() if goal.status == GoalStatus.ACTIVE]
        if active:
            return sorted(active, key=_goal_sort_key)[0]

        pending = [
            goal for goal in self._goals.values()
            if goal.status == GoalStatus.PENDING and goal.is_ready(current)
        ]
        return sorted(pending, key=_goal_sort_key)[0] if pending else None

    def get_waiting_goal(self) -> Goal | None:
        """Return one traceable deferred/active goal without activating it.

        The player loop uses this only for diagnostic WAIT decisions, so a
        navigation task never appears as an unexplained ``goal=-`` gap while
        its parent reply is still queued.
        """
        candidates = [
            goal for goal in self._goals.values()
            if goal.status in {GoalStatus.PENDING, GoalStatus.ACTIVE}
            and bool(goal.waiting_reason)
        ]
        return sorted(candidates, key=_goal_sort_key)[0] if candidates else None

    def get_blocked_goals(self) -> list[Goal]:
        """Return terminally blocked goals for explicit, safe external recovery.

        Callers must still decide whether a particular block may be resumed;
        this accessor deliberately performs no lifecycle mutation itself.
        """
        return sorted(
            [goal for goal in self._goals.values() if goal.status == GoalStatus.BLOCKED],
            key=_goal_sort_key,
        )

    def activate_goal(self, goal_id: str) -> Goal:
        goal = self._require_goal(goal_id)
        if goal.status == GoalStatus.PENDING:
            goal.status = GoalStatus.ACTIVE
            goal.attempts += 1
            goal.waiting_reason = None
        return goal

    def complete_goal(self, goal_id: str, *, reason: str | None = None) -> Goal:
        goal = self._require_goal(goal_id)
        goal.status = GoalStatus.COMPLETED
        goal.waiting_reason = None
        goal.blocked_reason = None
        if reason:
            goal.reason = reason
        return goal

    def fail_goal(
        self,
        goal_id: str,
        *,
        reason: str,
        retry: bool = True,
    ) -> Goal:
        goal = self._require_goal(goal_id)
        goal.last_error = reason
        goal.waiting_reason = None
        if retry and goal.attempts < goal.max_attempts:
            goal.status = GoalStatus.PENDING
        else:
            goal.status = GoalStatus.FAILED
        return goal

    def block_goal(self, goal_id: str, *, reason: str) -> Goal:
        goal = self._require_goal(goal_id)
        goal.status = GoalStatus.BLOCKED
        goal.last_error = reason
        goal.blocked_reason = reason
        goal.waiting_reason = None
        return goal

    def cancel_goal(self, goal_id: str, *, reason: str = "cancelled") -> Goal:
        goal = self._require_goal(goal_id)
        goal.status = GoalStatus.CANCELLED
        goal.last_error = reason
        goal.blocked_reason = reason
        goal.waiting_reason = None
        return goal

    def requeue_goal(self, goal_id: str) -> Goal:
        goal = self._require_goal(goal_id)
        if goal.status == GoalStatus.ACTIVE:
            goal.status = GoalStatus.PENDING
        goal.waiting_reason = None
        return goal

    def defer_goal(
        self,
        goal_id: str,
        *,
        reason: str,
        not_before: float,
    ) -> Goal:
        """Return an active goal to the scheduler with a named wait state."""
        goal = self._require_goal(goal_id)
        if goal.status == GoalStatus.ACTIVE:
            goal.status = GoalStatus.PENDING
        goal.not_before = float(not_before)
        goal.waiting_reason = reason
        return goal

    def resume_goal(self, goal_id: str, *, reason: str = "resumed") -> Goal:
        goal = self._require_goal(goal_id)
        if goal.status in {GoalStatus.BLOCKED, GoalStatus.ACTIVE}:
            goal.status = GoalStatus.PENDING
        goal.not_before = None
        goal.waiting_reason = None
        goal.blocked_reason = None
        goal.last_error = reason
        return goal

    def snapshot(self) -> list[dict[str, Any]]:
        return [goal.to_dict() for goal in sorted(self._goals.values(), key=_goal_sort_key)]

    def _find_duplicate(self, candidate: Goal) -> Goal | None:
        key = _dedupe_key(candidate)
        if key is None:
            return None
        for goal in self._goals.values():
            if (
                goal.goal_type == candidate.goal_type
                and (
                    goal.status in {GoalStatus.PENDING, GoalStatus.ACTIVE}
                    or (
                        goal.status == GoalStatus.BLOCKED
                        and bool(goal.payload.get("accept_message_merge_when_blocked"))
                    )
                )
                and _dedupe_key(goal) == key
            ):
                return goal
        return None

    def _require_goal(self, goal_id: str) -> Goal:
        goal = self.get_goal(goal_id)
        if goal is None:
            raise KeyError(f"Unknown goal: {goal_id}")
        return goal


def _dedupe_key(goal: Goal) -> str | None:
    value = goal.payload.get("dedupe_key")
    if value:
        return str(value)
    if goal.goal_type in {
        GoalType.ANSWER_PRIVATE_MESSAGE,
        GoalType.ANSWER_ALLIANCE_MESSAGE,
    }:
        room_id = goal.payload.get("room_id")
        sender_uid = goal.payload.get("sender_uid")
        if room_id and sender_uid:
            return f"pm:{room_id}:{sender_uid}"
    return None


def _merge_payload(target: dict[str, Any], incoming: Mapping[str, Any]) -> None:
    incoming_messages = incoming.get("messages")
    if isinstance(incoming_messages, list):
        messages = target.setdefault("messages", [])
        if isinstance(messages, list):
            known = {
                str(item.get("source_message_key"))
                for item in messages
                if isinstance(item, Mapping)
            }
            for message in incoming_messages:
                key = str(message.get("source_message_key")) if isinstance(message, Mapping) else ""
                if not key or key not in known:
                    messages.append(message)
                    if key:
                        known.add(key)
    # A private-message batch may contain more than one command or memory
    # candidate. Keep that evidence instead of allowing a later ordinary
    # message (for example, "my name is Alex") to erase an earlier command.
    collection_keys = {"parsed_commands", "pending_commands", "memory_candidates"}
    for key in collection_keys:
        values = incoming.get(key)
        if not isinstance(values, list):
            continue
        target_values = target.setdefault(key, [])
        if not isinstance(target_values, list):
            target_values = []
            target[key] = target_values
        known = {_payload_item_key(item) for item in target_values}
        for value in values:
            item_key = _payload_item_key(value)
            if item_key not in known:
                target_values.append(value)
                known.add(item_key)
    for key, value in incoming.items():
        if key in {"messages", *collection_keys}:
            continue
        # Preserve a known pending action when the newest message simply has
        # no command. The plural collections above retain the complete batch.
        if key in {"parsed_command", "pending_command"} and value is None and target.get(key) is not None:
            continue
        target[key] = value


def _payload_item_key(value: Any) -> str:
    if isinstance(value, Mapping):
        command_id = value.get("command_id")
        if command_id:
            return f"id:{command_id}"
        try:
            return repr(sorted(value.items()))
        except TypeError:
            return repr(value)
    return repr(value)


def _latest_expiry(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


def _latest_not_before(first: float | None, second: float | None) -> float | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


def _goal_sort_key(goal: Goal) -> tuple[int, float, str]:
    return (-int(goal.priority), goal.created_at, goal.goal_id)

