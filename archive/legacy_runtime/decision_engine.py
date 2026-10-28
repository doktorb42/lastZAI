"""Hybrid deterministic decision layer for PEva v2 Stage 1."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from archive.legacy_runtime.goal_manager import Goal, GoalStatus, GoalType


class DecisionType(str, Enum):
    NO_OP = "NO_OP"
    IGNORE = "IGNORE"
    GENERATE_REPLY = "GENERATE_REPLY"
    SEND_PRIVATE_REPLY = "SEND_PRIVATE_REPLY"
    NAVIGATE_PRIVATE_REPLY = "NAVIGATE_PRIVATE_REPLY"
    OPEN_CHAT = "OPEN_CHAT"
    OPEN_PRIVATE_CHAT = "OPEN_PRIVATE_CHAT"
    PREPARE_PRIVATE_REPLY_TARGET = "PREPARE_PRIVATE_REPLY_TARGET"
    WAIT = "WAIT"
    ASK_USER = "ASK_USER"


@dataclass(slots=True)
class Decision:
    decision_type: DecisionType
    reason: str
    goal_id: str | None = None
    action: dict[str, Any] | None = None
    confidence: float = 1.0
    requires_user_confirmation: bool = False
    wait_state: str | None = None
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "goal_id": self.goal_id,
            "decision_type": self.decision_type.value,
            "action": self.action,
            "reason": self.reason,
            "confidence": self.confidence,
            "requires_user_confirmation": self.requires_user_confirmation,
            "wait_state": self.wait_state,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class NeedUserDecision:
    decision_id: str
    question: str
    reason: str
    options: list[str]
    created_at: float
    related_goal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "question": self.question,
            "reason": self.reason,
            "options": list(self.options),
            "created_at": self.created_at,
            "related_goal_id": self.related_goal_id,
        }


class DecisionEngine:
    """Use code for safety/flow; reserve the existing AI for reply content."""

    def decide(
        self,
        world_state: Any,
        goal: Goal | None,
        context: Mapping[str, Any] | None = None,
    ) -> Decision:
        context = context or {}
        if goal is None:
            return self._decision(DecisionType.WAIT, "No pending goal.", None)

        if goal.status not in {GoalStatus.PENDING, GoalStatus.ACTIVE}:
            return self._decision(DecisionType.NO_OP, f"Goal is {goal.status.value}.", goal)

        if goal.goal_type == GoalType.ASK_USER:
            return self._decision(
                DecisionType.WAIT,
                goal.reason or "A user decision is pending.",
                goal,
                wait_state="WAITING_FOR_USER_DECISION",
            )

        if goal.goal_type == GoalType.WAIT:
            return self._decision(DecisionType.WAIT, goal.reason or "Wait goal.", goal)

        if goal.goal_type in {GoalType.ANSWER_PRIVATE_MESSAGE, GoalType.PROACTIVE_PRIVATE_MESSAGE}:
            if bool(context.get("send_lane_blocked", False)):
                return self._decision(
                    DecisionType.WAIT,
                    "Private send lane is paused for an unresolved delivery decision.",
                    goal,
                    wait_state="WAITING_FOR_USER_DECISION",
                )
            pending_reply_id = context.get("pending_reply_id")
            if pending_reply_id:
                if not bool(context.get("auto_send", False)):
                    return self._decision(DecisionType.WAIT, "Preview never sends replies.", goal)
                if not _navigation_dependencies_ready(world_state):
                    return self._decision(
                        DecisionType.WAIT,
                        "Private navigation is blocked until ADB is available and the UI is idle.",
                        goal,
                        wait_state="WAITING_FOR_DEPENDENCY",
                    )
                return self._decision(
                    DecisionType.NAVIGATE_PRIVATE_REPLY,
                    "A persistent navigation task will re-plan delivery from the observed UI state.",
                    goal,
                )
            if goal.goal_type == GoalType.PROACTIVE_PRIVATE_MESSAGE:
                return self._decision(
                    DecisionType.GENERATE_REPLY,
                    "A Python-selected proactive candidate needs text from the existing local AI.",
                    goal,
                )
            return self._decision(
                DecisionType.GENERATE_REPLY,
                "A private message needs reply text from the existing agent/AI.",
                goal,
            )

        if goal.goal_type == GoalType.ANSWER_ALLIANCE_MESSAGE:
            # Stage 2 deliberately has no SEND_ALLIANCE decision. The result
            # is always a preview candidate, even if PM auto-send is enabled.
            return self._decision(
                DecisionType.GENERATE_REPLY,
                "A selected alliance message needs a preview reply candidate.",
                goal,
            )

        if goal.goal_type in {GoalType.CHECK_PRIVATE_CHAT, GoalType.CHECK_ALLIANCE_CHAT}:
            screen = str(getattr(world_state, "screen", "UNKNOWN"))
            if not _safe_for_risky_action(world_state):
                return self._decision(
                    DecisionType.WAIT,
                    "Navigation is fail-closed while screen/ADB state is unsafe or unknown.",
                    goal,
                    wait_state="WAITING_FOR_DEPENDENCY",
                )
            if screen in {"HQ_BASE", "WORLD_MAP"}:
                return self._decision(
                    DecisionType.OPEN_CHAT,
                    "Known base/map screen can safely open chat.",
                    goal,
                    action={"action": "open_chat"},
                )
            if screen == "ALLIANCE_CHAT" and goal.goal_type == GoalType.CHECK_PRIVATE_CHAT:
                return self._decision(
                    DecisionType.OPEN_PRIVATE_CHAT,
                    "Known alliance chat can safely open the private tab.",
                    goal,
                    action={"action": "open_private_chat"},
                )
            return self._decision(DecisionType.WAIT, "Requested chat state is already open or not actionable.", goal)

        return self._decision(DecisionType.WAIT, "No Stage 1 action is defined for this goal.", goal)

    @staticmethod
    def _decision(
        decision_type: DecisionType,
        reason: str,
        goal: Goal | None,
        *,
        action: dict[str, Any] | None = None,
        requires_confirmation: bool = False,
        wait_state: str | None = None,
    ) -> Decision:
        return Decision(
            decision_type=decision_type,
            reason=reason,
            goal_id=goal.goal_id if goal else None,
            action=action,
            requires_user_confirmation=requires_confirmation,
            wait_state=wait_state,
        )


def _safe_for_risky_action(world_state: Any) -> bool:
    safe = getattr(world_state, "is_safe_for_navigation", None)
    return bool(safe()) if callable(safe) else False


def _navigation_dependencies_ready(world_state: Any) -> bool:
    return bool(
        getattr(world_state, "adb_connected", False)
        and not bool(getattr(world_state, "game_busy", False))
    )

