"""Persistent, target-driven navigation for PEva's verified PM delivery.

The navigator owns routing policy; it never generates reply text and never
performs ADB actions itself.  It records a serialisable task on the parent
goal, plans exactly one safe next step from the *observed* UI state, and asks
the PlayerLoop to observe again after every physical action.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class NavigationTargetType(str, Enum):
    WORLD_MAP = "WORLD_MAP"
    HQ_BASE = "HQ_BASE"
    ALLIANCE_CHAT = "ALLIANCE_CHAT"
    PRIVATE_CHAT = "PRIVATE_CHAT"
    PRIVATE_CONVERSATION = "PRIVATE_CONVERSATION"


class NavigationStatus(str, Enum):
    NAVIGATION_PENDING = "NAVIGATION_PENDING"
    NAVIGATION_IN_PROGRESS = "NAVIGATION_IN_PROGRESS"
    WAITING_FOR_UI_TRANSITION = "WAITING_FOR_UI_TRANSITION"
    WAITING_FOR_OBSERVATION = "WAITING_FOR_OBSERVATION"
    REPLANNING = "REPLANNING"
    RECOVERING_TO_ANCHOR = "RECOVERING_TO_ANCHOR"
    TARGET_REACHED = "TARGET_REACHED"
    NAVIGATION_BLOCKED = "NAVIGATION_BLOCKED"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"


@dataclass(slots=True)
class NavigationTask:
    navigation_id: str
    goal_id: str
    target_type: str
    target_uid: str
    target_room_id: str
    status: str = NavigationStatus.NAVIGATION_PENDING.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    current_route: list[str] = field(default_factory=list)
    attempt_count: int = 0
    replan_count: int = 0
    recovery_count: int = 0
    unknown_observations: int = 0
    same_state_failures: int = 0
    observation_count: int = 0
    last_known_screen: str | None = None
    last_verified_screen: str | None = None
    last_observed_screen: str | None = None
    last_action: str | None = None
    last_action_screen: str | None = None
    last_action_result: str | None = None
    blocked_reason: str | None = None
    failure_reason: str | None = None
    awaiting_observation: bool = False

    # One physical navigation action may still be settling after its ADB call
    # returns.  These fields are deliberately serialisable with the parent
    # goal, so an in-flight transition cannot be mistaken for a missing goal.
    ui_effect_active: bool = False
    ui_effect_action_id: str | None = None
    ui_effect_source_screen: str | None = None
    ui_effect_action_name: str | None = None
    ui_effect_started_at: float | None = None
    ui_effect_deadline_at: float | None = None
    ui_effect_allowed_result_screens: list[str] = field(default_factory=list)
    ui_effect_observations_seen: int = 0
    ui_effect_last_observation: str | None = None
    ui_effect_last_confidence: float | None = None
    ui_effect_outcome: str | None = None
    ui_effect_result_screen: str | None = None
    ui_effect_result_classification: str | None = None
    ui_effect_elapsed_ms: int | None = None

    # A candidate navigation block gets a final observation grace window.  A
    # late but meaningful UI transition can therefore resume the target rather
    # than turn a settling screen into a terminal failure.
    pending_block_reason: str | None = None
    block_grace_source_screen: str | None = None
    block_grace_deadline_at: float | None = None
    block_grace_observations_seen: int = 0

    # PM rows are click-sensitive.  Keep a compact streak rather than relying
    # on the last action as evidence that the private list has settled.
    stable_screen: str | None = None
    stable_screen_samples: int = 0
    target_select_retries: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NavigationTask":
        fields = {item.name for item in cls.__dataclass_fields__.values()}
        data = {key: value[key] for key in fields if key in value}
        data["current_route"] = list(data.get("current_route") or [])
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def touch(self, now: float) -> None:
        self.updated_at = float(now)


@dataclass(frozen=True, slots=True)
class NavigationEdge:
    allowed_from: frozenset[str]
    action: str
    expected_possible_results: frozenset[str]
    verification: str
    risk: str
    fallbacks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NavigationPlan:
    kind: str
    reason: str
    task: NavigationTask
    action: str | None = None
    expected_possible_results: tuple[str, ...] = ()
    wait_seconds: float = 0.0


CHAT_SCREENS = frozenset({
    "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION", "CHAT_OPEN_UNCLASSIFIED",
})
SAFE_ANCHORS = frozenset({"WORLD_MAP", "HQ_BASE"})

# An edge describes a legal *attempt*, not one assumed output.  Every result
# in expected_possible_results is re-observed and re-planned from reality.
NAVIGATION_GRAPH: dict[str, NavigationEdge] = {
    "open_chat": NavigationEdge(
        allowed_from=frozenset({"WORLD_MAP", "HQ_BASE"}),
        action="open_chat",
        expected_possible_results=frozenset({
            "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION", "CHAT_OPEN_UNCLASSIFIED",
        }),
        verification="fresh screen observation",
        risk="low",
        fallbacks=("reobserve", "recover_to_anchor"),
    ),
    "open_private_chat": NavigationEdge(
        allowed_from=frozenset({"ALLIANCE_CHAT", "CHAT_OPEN_UNCLASSIFIED"}),
        action="open_private_chat",
        expected_possible_results=frozenset({
            "PRIVATE_CHAT", "PRIVATE_CONVERSATION", "CHAT_OPEN_UNCLASSIFIED",
        }),
        verification="fresh screen observation",
        risk="low",
        fallbacks=("reobserve", "recover_to_anchor"),
    ),
    "prepare_private_reply_target": NavigationEdge(
        allowed_from=frozenset({"PRIVATE_CHAT", "PRIVATE_CONVERSATION"}),
        action="prepare_private_reply_target",
        expected_possible_results=frozenset({"PRIVATE_CONVERSATION"}),
        verification="UID click binding plus fresh screen observation",
        risk="low",
        fallbacks=("back_then_select_uid", "reobserve"),
    ),
    "recover_back": NavigationEdge(
        allowed_from=frozenset({"UNKNOWN", "CHAT_OPEN_UNCLASSIFIED", *CHAT_SCREENS}),
        action="recover_back",
        expected_possible_results=frozenset({
            "PRIVATE_CHAT", "ALLIANCE_CHAT", "WORLD_MAP", "HQ_BASE", "CHAT_OPEN_UNCLASSIFIED",
        }),
        verification="fresh screen observation",
        risk="controlled",
        fallbacks=("reobserve", "navigation_blocked"),
    ),
}


class NavigationManager:
    """Plan PM navigation while keeping its parent reply goal persistent."""

    payload_key = "navigation_task"

    def __init__(
        self,
        *,
        now: callable = time.time,
        max_unknown_observations: int = 3,
        max_recovery_attempts: int = 3,
        max_same_state_attempts: int = 2,
        ui_effect_timeouts: Mapping[str, float] | None = None,
        block_grace_seconds: float = 0.75,
        min_private_chat_samples: int = 2,
        min_private_chat_confidence: float = 0.70,
    ) -> None:
        self.now = now
        self.max_unknown_observations = max(1, int(max_unknown_observations))
        self.max_recovery_attempts = max(1, int(max_recovery_attempts))
        self.max_same_state_attempts = max(1, int(max_same_state_attempts))
        self.ui_effect_timeouts = {
            "open_chat": 2.0,
            "open_private_chat": 2.0,
            "recover_back": 2.0,
            "prepare_private_reply_target": 2.0,
            **dict(ui_effect_timeouts or {}),
        }
        self.block_grace_seconds = max(0.1, float(block_grace_seconds))
        self.min_private_chat_samples = max(1, int(min_private_chat_samples))
        self.min_private_chat_confidence = float(min_private_chat_confidence)

    def ensure_private_conversation_task(self, goal: Any) -> NavigationTask:
        raw = goal.payload.get(self.payload_key)
        if isinstance(raw, Mapping):
            task = NavigationTask.from_mapping(raw)
            if (
                task.goal_id == goal.goal_id
                and task.target_uid == str(goal.payload.get("sender_uid") or "")
                and task.target_room_id == str(goal.payload.get("room_id") or "")
            ):
                return task
        now = float(self.now())
        task = NavigationTask(
            navigation_id=uuid.uuid4().hex,
            goal_id=goal.goal_id,
            target_type=NavigationTargetType.PRIVATE_CONVERSATION.value,
            target_uid=str(goal.payload.get("sender_uid") or ""),
            target_room_id=str(goal.payload.get("room_id") or ""),
            created_at=now,
            updated_at=now,
        )
        self._store(goal, task)
        return task

    def plan_private_conversation(
        self,
        goal: Any,
        world_state: Any,
        *,
        observed_this_tick: bool,
    ) -> NavigationPlan:
        task = self.ensure_private_conversation_task(goal)
        screen = str(getattr(world_state, "screen", "UNKNOWN") or "UNKNOWN").upper()
        now = float(self.now())
        self._note_observation(task, world_state, observed_this_tick, now)

        # Never turn an incomplete target into a coordinate action.  The
        # delivery layer validates the room too, but planning must fail closed
        # before it can request any physical UI step.
        if not task.target_uid or not task.target_room_id:
            return self._block(goal, task, "navigation_target_incomplete", now)

        if task.status in {
            NavigationStatus.NAVIGATION_BLOCKED.value,
            NavigationStatus.NAVIGATION_FAILED.value,
        }:
            return NavigationPlan("BLOCKED", task.blocked_reason or task.failure_reason or "navigation_terminal", task)

        effect_wait = self._observe_pending_ui_effect(
            goal, task, world_state, observed_this_tick, now
        )
        if effect_wait is not None:
            return effect_wait

        block_wait = self._observe_block_grace(
            goal, task, world_state, observed_this_tick, now
        )
        if block_wait is not None:
            return block_wait

        if task.awaiting_observation:
            if not observed_this_tick:
                task.status = NavigationStatus.WAITING_FOR_OBSERVATION.value
                task.touch(now)
                self._store(goal, task)
                return NavigationPlan("WAIT", "awaiting_post_action_observation", task)
            task.awaiting_observation = False
            task.replan_count += 1
            task.status = NavigationStatus.REPLANNING.value

        if self._target_is_verified(task, world_state):
            task.status = NavigationStatus.TARGET_REACHED.value
            task.last_verified_screen = screen
            task.touch(now)
            self._store(goal, task)
            return NavigationPlan("TARGET_REACHED", "verified_target_conversation", task)

        if screen == "UNKNOWN":
            return self._plan_unknown(goal, task, world_state, now)

        if screen == "WORLD_MAP" or screen == "HQ_BASE":
            return self._plan_edge(goal, task, world_state, "open_chat", now)
        if screen == "ALLIANCE_CHAT":
            return self._plan_edge(goal, task, world_state, "open_private_chat", now)
        if screen == "CHAT_OPEN_UNCLASSIFIED":
            if bool(getattr(world_state, "chat_shell_evidence", False)):
                return self._plan_edge(goal, task, world_state, "open_private_chat", now)
            return self._wait(goal, task, "chat_shell_not_verified", now)
        if screen == "PRIVATE_CONVERSATION":
            # A conversation view without an exact UID/room binding is not a
            # partially-completed target.  First take one controlled Back and
            # observe the actual destination.  This deliberately keeps the
            # lower-level UID selector from doing an unobserved Back+click
            # sequence inside one abstract navigation step.
            return self._plan_edge(goal, task, world_state, "recover_back", now)
        if screen == "PRIVATE_CHAT":
            stability_wait = self._private_chat_stability_wait(
                goal, task, world_state, now
            )
            if stability_wait is not None:
                return stability_wait
            return self._plan_edge(goal, task, world_state, "prepare_private_reply_target", now)

        return self._block(goal, task, f"unsupported_navigation_screen:{screen}", now)

    def action_started(
        self,
        goal: Any,
        task: NavigationTask,
        action: str,
        screen: str,
        *,
        action_id: str | None = None,
        allowed_result_screens: tuple[str, ...] = (),
    ) -> None:
        task.status = NavigationStatus.NAVIGATION_IN_PROGRESS.value
        task.last_action = action
        task.last_action_screen = str(screen or "UNKNOWN").upper()
        task.attempt_count += 1
        if action == "recover_back":
            # Count the controlled recovery attempt when it is issued, not
            # only when its transport happens to fail.  A Back that leaves us
            # on UNKNOWN is still one consumed recovery opportunity.
            task.recovery_count += 1
        task.current_route.append(f"{task.last_action_screen}:{action}")
        task.current_route[:] = task.current_route[-24:]
        task.ui_effect_active = False
        task.ui_effect_action_id = str(action_id or "") or None
        task.ui_effect_source_screen = task.last_action_screen
        task.ui_effect_action_name = action
        task.ui_effect_started_at = float(self.now())
        task.ui_effect_deadline_at = None
        task.ui_effect_allowed_result_screens = [
            str(value).upper() for value in allowed_result_screens
        ]
        task.ui_effect_observations_seen = 0
        task.ui_effect_last_observation = None
        task.ui_effect_last_confidence = None
        task.ui_effect_outcome = None
        task.ui_effect_result_screen = None
        task.ui_effect_result_classification = None
        task.ui_effect_elapsed_ms = None
        task.touch(float(self.now()))
        self._store(goal, task)

    def action_result(
        self,
        goal: Any,
        action: Mapping[str, Any],
        *,
        ok: bool,
        result: str,
        reason: str | None = None,
    ) -> NavigationTask:
        task = self.ensure_private_conversation_task(goal)
        action_name = str(action.get("action") or "unknown")
        task.last_action = action_name
        task.last_action_result = str(result)
        reason_text = str(reason or result)
        pending_click_effect = (
            action_name == "prepare_private_reply_target"
            and "click_not_registered" in reason_text
        )
        if not ok and not pending_click_effect:
            task.failure_reason = str(reason or result)
            self._clear_ui_effect(task)
            task.status = NavigationStatus.REPLANNING.value
        else:
            task.failure_reason = None if ok else reason_text
            task.status = NavigationStatus.WAITING_FOR_UI_TRANSITION.value
            started_at = task.ui_effect_started_at or float(self.now())
            task.ui_effect_active = True
            task.ui_effect_started_at = started_at
            task.ui_effect_deadline_at = started_at + float(
                self.ui_effect_timeouts.get(action_name, 2.0)
            )
            if not task.ui_effect_allowed_result_screens:
                edge = NAVIGATION_GRAPH.get(action_name)
                task.ui_effect_allowed_result_screens = list(
                    sorted(edge.expected_possible_results) if edge else []
                )
        task.awaiting_observation = True
        task.touch(float(self.now()))
        self._store(goal, task)
        return task

    def observation_after_action(self, goal: Any, world_state: Any) -> NavigationTask:
        """Compatibility hook for callers that explicitly supplied an observation."""
        task = self.ensure_private_conversation_task(goal)
        self._note_observation(task, world_state, True, float(self.now()))
        # Do not clear an active effect here: the caller may have captured a
        # stale first frame and must remain observation-only until it settles.
        self._store(goal, task)
        return task

    def reset_after_wrong_screen_delivery(self, goal: Any, reason: str) -> NavigationTask:
        task = self.ensure_private_conversation_task(goal)
        task.status = NavigationStatus.REPLANNING.value
        task.failure_reason = str(reason)
        task.awaiting_observation = False
        task.touch(float(self.now()))
        self._store(goal, task)
        return task

    def resume_after_external_observation(self, goal: Any) -> NavigationTask:
        """Safely revive a no-send navigation block after a real UI change."""
        task = self.ensure_private_conversation_task(goal)
        task.status = NavigationStatus.REPLANNING.value
        task.blocked_reason = None
        task.failure_reason = None
        task.pending_block_reason = None
        task.block_grace_source_screen = None
        task.block_grace_deadline_at = None
        task.block_grace_observations_seen = 0
        task.same_state_failures = 0
        task.awaiting_observation = False
        self._clear_ui_effect(task)
        task.touch(float(self.now()))
        self._store(goal, task)
        return task

    def _plan_unknown(
        self,
        goal: Any,
        task: NavigationTask,
        world_state: Any,
        now: float,
    ) -> NavigationPlan:
        if task.unknown_observations < self.max_unknown_observations:
            return self._wait(goal, task, "transient_unknown_reobserve", now)

        last_known = str(getattr(world_state, "last_known_screen", "") or task.last_known_screen or "")
        can_recover_back = (
            last_known in CHAT_SCREENS
            or bool(getattr(world_state, "chat_shell_evidence", False))
        )
        if can_recover_back and task.recovery_count < self.max_recovery_attempts:
            task.status = NavigationStatus.RECOVERING_TO_ANCHOR.value
            return self._plan_edge(goal, task, world_state, "recover_back", now)
        if task.recovery_count >= self.max_recovery_attempts:
            return self._start_block_grace(goal, task, "safe_anchor_recovery_exhausted", screen="UNKNOWN", now=now)
        return self._start_block_grace(goal, task, "unknown_without_safe_recovery_evidence", screen="UNKNOWN", now=now)

    def _plan_edge(
        self,
        goal: Any,
        task: NavigationTask,
        world_state: Any,
        edge_name: str,
        now: float,
    ) -> NavigationPlan:
        edge = NAVIGATION_GRAPH[edge_name]
        screen = str(getattr(world_state, "screen", "UNKNOWN") or "UNKNOWN").upper()
        if screen not in edge.allowed_from:
            return self._block(goal, task, f"edge_precondition_failed:{edge_name}:{screen}", now)

        timed_out_same_action = (
            task.last_action == edge.action
            and task.last_action_screen == screen
            and task.last_action_result == "ui_effect_timeout"
        )
        if timed_out_same_action:
            if edge.action == "prepare_private_reply_target" and task.target_select_retries < 1:
                # A row tap whose click event was not registered gets exactly
                # one retry, only after a complete transition window.
                task.target_select_retries += 1
            elif edge.action in {"open_chat", "recover_back"}:
                return self._start_block_grace(
                    goal,
                    task,
                    f"same_state_action_exhausted:{edge.action}:{screen}",
                    screen=screen,
                    now=now,
                )
            elif screen in CHAT_SCREENS and task.recovery_count < self.max_recovery_attempts:
                edge = NAVIGATION_GRAPH["recover_back"]
                edge_name = "recover_back"
            else:
                return self._start_block_grace(
                    goal,
                    task,
                    f"same_state_action_exhausted:{edge.action}:{screen}",
                    screen=screen,
                    now=now,
                )

        task.status = (
            NavigationStatus.RECOVERING_TO_ANCHOR.value
            if edge.action == "recover_back"
            else NavigationStatus.NAVIGATION_PENDING.value
        )
        task.touch(now)
        self._store(goal, task)
        return NavigationPlan(
            "ACTION",
            f"route_from_{screen}",
            task,
            action=edge.action,
            expected_possible_results=tuple(sorted(edge.expected_possible_results)),
        )

    def _wait(self, goal: Any, task: NavigationTask, reason: str, now: float) -> NavigationPlan:
        task.status = NavigationStatus.WAITING_FOR_OBSERVATION.value
        task.touch(now)
        self._store(goal, task)
        return NavigationPlan("WAIT", reason, task)

    def _block(self, goal: Any, task: NavigationTask, reason: str, now: float) -> NavigationPlan:
        task.status = NavigationStatus.NAVIGATION_BLOCKED.value
        task.blocked_reason = reason
        task.touch(now)
        self._store(goal, task)
        return NavigationPlan("BLOCKED", reason, task)

    def _observe_pending_ui_effect(
        self,
        goal: Any,
        task: NavigationTask,
        world_state: Any,
        observed_this_tick: bool,
        now: float,
    ) -> NavigationPlan | None:
        if not task.ui_effect_active:
            return None
        screen = str(getattr(world_state, "screen", "UNKNOWN") or "UNKNOWN").upper()
        if observed_this_tick:
            task.ui_effect_observations_seen += 1
            task.ui_effect_last_observation = screen
            confidence = getattr(world_state, "screen_confidence", None)
            task.ui_effect_last_confidence = _optional_float(confidence)
            if self._is_effect_result(task, screen):
                classification = self._classify_ui_effect_result(task, screen)
                self._finish_ui_effect(
                    task,
                    "confirmed",
                    screen,
                    now,
                    classification=classification,
                )
                if classification:
                    task.failure_reason = classification
                task.replan_count += 1
                task.same_state_failures = 0
                self._store(goal, task)
                return None

        deadline = float(task.ui_effect_deadline_at or now)
        if now < deadline:
            task.status = NavigationStatus.WAITING_FOR_UI_TRANSITION.value
            task.awaiting_observation = True
            task.touch(now)
            self._store(goal, task)
            return NavigationPlan(
                "WAIT",
                "waiting_for_ui_effect",
                task,
                wait_seconds=max(0.0, deadline - now),
            )

        # The full observation window elapsed.  Only now does a stale source
        # screen count as a same-state failure; a first frame never does.
        if str(task.ui_effect_last_observation or screen).upper() == str(task.ui_effect_source_screen or "").upper():
            task.same_state_failures += 1
        task.replan_count += 1
        classification = self._classify_ui_effect_timeout(task, screen)
        task.failure_reason = classification or f"ui_effect_timeout:{task.ui_effect_action_name or 'unknown'}"
        task.last_action_result = "ui_effect_timeout"
        self._finish_ui_effect(
            task,
            "timeout",
            screen,
            now,
            classification=classification,
        )
        self._store(goal, task)
        return None

    def _observe_block_grace(
        self,
        goal: Any,
        task: NavigationTask,
        world_state: Any,
        observed_this_tick: bool,
        now: float,
    ) -> NavigationPlan | None:
        if not task.pending_block_reason:
            return None
        screen = str(getattr(world_state, "screen", "UNKNOWN") or "UNKNOWN").upper()
        source = str(task.block_grace_source_screen or "UNKNOWN").upper()
        if observed_this_tick:
            task.block_grace_observations_seen += 1
            if screen != source:
                task.pending_block_reason = None
                task.block_grace_source_screen = None
                task.block_grace_deadline_at = None
                task.block_grace_observations_seen = 0
                task.same_state_failures = 0
                task.status = NavigationStatus.REPLANNING.value
                task.replan_count += 1
                task.touch(now)
                self._store(goal, task)
                return None
        if now < float(task.block_grace_deadline_at or now):
            task.status = NavigationStatus.WAITING_FOR_UI_TRANSITION.value
            task.touch(now)
            self._store(goal, task)
            return NavigationPlan("WAIT", "navigation_block_grace", task)
        reason = task.pending_block_reason
        task.pending_block_reason = None
        task.block_grace_source_screen = None
        task.block_grace_deadline_at = None
        return self._block(goal, task, reason, now)

    def _start_block_grace(
        self,
        goal: Any,
        task: NavigationTask,
        reason: str,
        *,
        screen: str,
        now: float,
    ) -> NavigationPlan:
        if task.pending_block_reason == reason:
            return self._observe_block_grace(goal, task, _ScreenOnly(screen), False, now) or NavigationPlan(
                "WAIT", "navigation_block_grace", task
            )
        task.pending_block_reason = reason
        task.block_grace_source_screen = str(screen).upper()
        task.block_grace_deadline_at = now + self.block_grace_seconds
        task.block_grace_observations_seen = 0
        task.status = NavigationStatus.WAITING_FOR_UI_TRANSITION.value
        task.touch(now)
        self._store(goal, task)
        return NavigationPlan("WAIT", "navigation_block_grace", task, wait_seconds=self.block_grace_seconds)

    def _private_chat_stability_wait(
        self,
        goal: Any,
        task: NavigationTask,
        world_state: Any,
        now: float,
    ) -> NavigationPlan | None:
        confidence = _optional_float(getattr(world_state, "screen_confidence", None))
        confidence_ok = confidence is None or confidence >= self.min_private_chat_confidence
        shell_ok = bool(getattr(world_state, "chat_shell_evidence", False))
        stable = (
            task.stable_screen == "PRIVATE_CHAT"
            and task.stable_screen_samples >= self.min_private_chat_samples
            and confidence_ok
            and shell_ok
        )
        if stable:
            return None
        task.status = NavigationStatus.WAITING_FOR_OBSERVATION.value
        task.touch(now)
        self._store(goal, task)
        return NavigationPlan(
            "WAIT",
            "private_chat_stability",
            task,
        )

    @staticmethod
    def _is_effect_result(task: NavigationTask, screen: str) -> bool:
        source = str(task.ui_effect_source_screen or "UNKNOWN").upper()
        if screen == source:
            return False
        if screen == "UNKNOWN":
            return False
        allowed = {str(value).upper() for value in task.ui_effect_allowed_result_screens}
        # A known unexpected destination is still an actual UI effect.  It is
        # safe to stop waiting and re-plan from it rather than repeat the tap.
        return not allowed or screen in allowed or screen in SAFE_ANCHORS or screen in CHAT_SCREENS

    @staticmethod
    def _clear_ui_effect(task: NavigationTask) -> None:
        task.ui_effect_active = False
        task.ui_effect_deadline_at = None
        task.awaiting_observation = False

    @staticmethod
    def _classify_ui_effect_result(task: NavigationTask, screen: str) -> str | None:
        """Name delayed PM-row outcomes without treating them as delivery proof."""
        if task.ui_effect_action_name != "prepare_private_reply_target":
            return None
        click_was_missing = "click_not_registered" in str(task.failure_reason or "")
        if click_was_missing and screen == "PRIVATE_CONVERSATION":
            # A visual conversation change without a fresh matching UID event
            # is explicitly unverified and must go through recover_back.
            return "unverified_conversation_after_click_not_registered"
        if click_was_missing:
            # The physical UI changed, but UID proof never arrived. Re-plan
            # from the observed destination; it is not a send-capable target.
            return "stale_ui_or_unexpected_physical_effect"
        if screen == "PRIVATE_CONVERSATION":
            return "verified_target_observed"
        return "unexpected_target_transition"

    @staticmethod
    def _classify_ui_effect_timeout(task: NavigationTask, screen: str) -> str | None:
        if task.ui_effect_action_name != "prepare_private_reply_target":
            return None
        if "click_not_registered" not in str(task.failure_reason or ""):
            return None
        if screen == "PRIVATE_CHAT":
            return "click_not_registered_stable_private_chat"
        return "stale_ui_or_unexpected_physical_effect"

    def _finish_ui_effect(
        self,
        task: NavigationTask,
        outcome: str,
        screen: str,
        now: float,
        *,
        classification: str | None = None,
    ) -> None:
        started = task.ui_effect_started_at or now
        task.ui_effect_active = False
        task.awaiting_observation = False
        task.ui_effect_outcome = outcome
        task.ui_effect_result_screen = screen
        task.ui_effect_result_classification = classification
        task.ui_effect_elapsed_ms = max(0, int((now - started) * 1000))
        task.ui_effect_deadline_at = None
        task.status = NavigationStatus.REPLANNING.value
        task.touch(now)

    def _note_observation(
        self,
        task: NavigationTask,
        world_state: Any,
        observed_this_tick: bool,
        now: float,
    ) -> None:
        if not observed_this_tick:
            return
        screen = str(getattr(world_state, "screen", "UNKNOWN") or "UNKNOWN").upper()
        task.observation_count += 1
        task.last_observed_screen = screen
        if screen == "UNKNOWN":
            task.unknown_observations += 1
        else:
            task.unknown_observations = 0
            task.last_known_screen = screen
        if task.stable_screen == screen:
            task.stable_screen_samples += 1
        else:
            task.stable_screen = screen
            task.stable_screen_samples = 1
        task.touch(now)

    @staticmethod
    def _target_is_verified(task: NavigationTask, world_state: Any) -> bool:
        return (
            str(getattr(world_state, "screen", "UNKNOWN")) == "PRIVATE_CONVERSATION"
            and bool(getattr(world_state, "current_conversation_verified", False))
            and str(getattr(world_state, "current_conversation_uid", "") or "") == task.target_uid
            and str(getattr(world_state, "current_conversation_room_id", "") or "") == task.target_room_id
        )

    def _store(self, goal: Any, task: NavigationTask) -> None:
        goal.payload[self.payload_key] = task.to_dict()


@dataclass(frozen=True, slots=True)
class _ScreenOnly:
    screen: str


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
