"""Event-driven Stage 1 orchestration layered above the proven chat pipeline."""

from __future__ import annotations

import json
import queue
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from archive.legacy_runtime.action_ex import build_private_reply_action
from mozg.action_claim_guard import ActionClaimGuard
from mozg.action_claim_rewriter import ActionClaimRewriter
from trening.agency_core import AgencyCore
from trening.agency_store import AgencyStore
from trening.agency_types import AgencyDecision, ProactiveDecision, REACTIVE_WITH_MESSAGE
from mozg.command_parser import CommandParser, ParsedCommand
from archive.legacy_runtime.decision_engine import Decision, DecisionEngine, DecisionType
from rece.delivery_state import DeliveryState, delivery_metadata, is_ambiguous_delivery
from trening.feedback_store import FeedbackStore
from archive.legacy_runtime.goal_manager import Goal, GoalManager, GoalPriority, GoalStatus, GoalType
from charakter.humanization_store import HumanizationStore
from mozg.memory_review import MemoryReviewStore
from mozg.memory_candidates import MemoryCandidateDetector
from archive.legacy_runtime.message_logging import ai_final, ai_original, ai_reply, incoming_message
from mozg.message_intent import MessageAnalysis, MessageIntentAnalyzer
from archive.legacy_runtime.navigation_manager import NavigationManager
from mozg.pending_commands import PendingCommandGoal
from archive.legacy_runtime.player_events import EventType, PlayerEvent
from charakter.player_style import PlayerStyle
from charakter.personality import PevaPersonality, load_personality
from mozg.private_context import PrivateContextRegistry
from mozg.reply_naturalness import NaturalnessVerdict, ReplyNaturalness
from mozg.reply_quality_gate import ReplyQualityGate
from emocje.response_delay import ResponseDelayManager
from charakter.social_context import AllianceConversationContext
from charakter.social_initiative import InitiativeCandidate, SocialInitiativeManager, negative_social_signal
from charakter.social_policy import SocialDecision, SocialPolicy
from charakter.social_reply_policy import PrivateSocialReplyPolicy
from archive.legacy_runtime.user_decision import TerminalDecisionControl, UserDecisionManager, UserDecisionRequest
from archive.legacy_runtime.world_state import WorldState
from config.runtime_config import DATA_DIR, SELF_UID, is_direct_private_room


@dataclass(slots=True)
class PlayerLoopConfig:
    tick_interval: float = 0.75
    screen_poll_interval: float = 2.0
    ui_effect_poll_interval: float = 0.20
    auto_send: bool = False
    record_events: bool = False
    max_action_retries: int = 3
    clear_logcat: bool = True
    max_events_per_tick: int = 50
    private_batch_window: float = 1.1
    alliance_batch_window: float = 2.0
    social_debug: bool = False
    max_dependency_retries: int = 3
    recovery_retry_delay: float = 0.5
    enable_terminal_decision_control: bool = True
    humanization_enabled: bool = False
    naturalness_rewrite: bool = False
    response_delay: bool = False
    relationship_adaptation: bool = True
    memory_adaptation: bool = True
    proactive_enabled: bool = False
    proactive_auto_send: bool = False
    proactive_global_hourly_limit: int = 3
    proactive_same_player_min_minutes: int = 180
    proactive_same_topic_min_minutes: int = 720
    agency_enabled: bool = True
    agency_social_preview_fallback: bool = True


@dataclass(frozen=True, slots=True)
class PrivateReplySafety:
    original_reply: str
    final_reply: str
    guard_triggered: bool
    rewritten: bool
    rewrite_source: str
    guard_reason: str | None = None


class PlayerLoopRecorder:
    """Append-only optional dataset recorder; it creates files only when used."""

    def __init__(self, root: str | Path = "data/player-loop") -> None:
        self.root = Path(root)

    def record(self, category: str, payload: Mapping[str, Any]) -> None:
        if category not in {
            "events", "decisions", "actions", "message_analysis",
            "social_decisions", "memory_candidates",
        }:
            raise ValueError(f"Unsupported recorder category: {category}")
        self.root.mkdir(parents=True, exist_ok=True)
        record = {"recorded_at": time.time(), **dict(payload)}
        path = self.root / f"{category}.jsonl"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def record_raw_training(self, payload: Mapping[str, Any]) -> None:
        """Write reviewable raw examples separately from operational logs."""
        root = DATA_DIR / "training" / "raw"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "peva_private_raw.jsonl"
        record = {"recorded_at": time.time(), **dict(payload)}
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class PlayerLoop:
    """Small, fail-closed control loop for PEva v2 Stage 1.

    The loop never parses logcat. Existing ``ChatCapture`` supplies messages;
    existing ``LastZAgent`` owns SQLite/AI reply generation; existing
    ``ActionExecutor`` remains the only path for physical actions.
    """

    def __init__(
        self,
        *,
        capture: Any | None,
        agent: Any,
        executor: Any,
        screen_provider: Callable[[], Mapping[str, Any] | None] | None = None,
        config: PlayerLoopConfig | None = None,
        world_state: WorldState | None = None,
        goal_manager: GoalManager | None = None,
        decision_engine: DecisionEngine | None = None,
        navigation_manager: NavigationManager | None = None,
        intent_analyzer: MessageIntentAnalyzer | None = None,
        social_policy: SocialPolicy | None = None,
        alliance_context: AllianceConversationContext | None = None,
        memory_candidate_detector: MemoryCandidateDetector | None = None,
        private_contexts: PrivateContextRegistry | None = None,
        command_parser: CommandParser | None = None,
        action_claim_guard: ActionClaimGuard | None = None,
        action_claim_rewriter: ActionClaimRewriter | None = None,
        player_style: PlayerStyle | None = None,
        memory_review_store: MemoryReviewStore | None = None,
        feedback_store: FeedbackStore | None = None,
        user_decision_manager: UserDecisionManager | None = None,
        terminal_decision_control: TerminalDecisionControl | None = None,
        reply_quality_gate: ReplyQualityGate | None = None,
        humanization_store: HumanizationStore | None = None,
        reply_naturalness: ReplyNaturalness | None = None,
        response_delay_manager: ResponseDelayManager | None = None,
        social_initiative_manager: SocialInitiativeManager | None = None,
        private_social_reply_policy: PrivateSocialReplyPolicy | None = None,
        personality: PevaPersonality | None = None,
        agency_core: AgencyCore | None = None,
        agency_store: AgencyStore | None = None,
        recorder: PlayerLoopRecorder | None = None,
        logger: Callable[[str], None] = print,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.capture = capture
        self.agent = agent
        self.executor = executor
        self.screen_provider = screen_provider
        self.config = config or PlayerLoopConfig()
        self.world_state = world_state or WorldState()
        self.goal_manager = goal_manager or GoalManager()
        self.decision_engine = decision_engine or DecisionEngine()
        self.navigation_manager = navigation_manager or NavigationManager(
            now=now,
            max_recovery_attempts=max(1, int(self.config.max_action_retries)),
            max_same_state_attempts=max(1, int(self.config.max_action_retries) - 1),
        )
        self.intent_analyzer = intent_analyzer or MessageIntentAnalyzer(
            ai=getattr(agent, "ai", None),
        )
        self.social_policy = social_policy or SocialPolicy(now=now)
        self.alliance_context = alliance_context or AllianceConversationContext()
        self.memory_candidate_detector = memory_candidate_detector or MemoryCandidateDetector()
        self.private_contexts = private_contexts or PrivateContextRegistry()
        self.command_parser = command_parser or CommandParser()
        self.action_claim_guard = action_claim_guard or ActionClaimGuard()
        self.action_claim_rewriter = action_claim_rewriter or ActionClaimRewriter()
        self.player_style = player_style or PlayerStyle()
        self.memory_review_store = memory_review_store
        self.feedback_store = feedback_store
        self.user_decision_manager = user_decision_manager or UserDecisionManager(now=now)
        self.terminal_decision_control = terminal_decision_control
        self.reply_quality_gate = reply_quality_gate or ReplyQualityGate()
        self.humanization_store = humanization_store
        if self.humanization_store is None and (self.config.humanization_enabled or self.config.proactive_enabled or self.config.agency_enabled):
            self.humanization_store = HumanizationStore()
        self.reply_naturalness = reply_naturalness or ReplyNaturalness()
        self.response_delay_manager = response_delay_manager or ResponseDelayManager(
            enabled=self.config.response_delay,
        )
        self.social_initiative_manager = social_initiative_manager or SocialInitiativeManager(
            enabled=self.config.proactive_enabled,
            global_hourly_limit=self.config.proactive_global_hourly_limit,
            same_player_min_minutes=self.config.proactive_same_player_min_minutes,
            same_topic_min_minutes=self.config.proactive_same_topic_min_minutes,
            now=now,
        )
        self.private_social_reply_policy = private_social_reply_policy or PrivateSocialReplyPolicy()
        self.personality = personality or load_personality()
        self.agency_core = agency_core or AgencyCore(
            complete=getattr(getattr(agent, "ai", None), "complete_task_safe", None),
        )
        self.agency_store = agency_store
        if self.agency_store is None and self.config.agency_enabled:
            self.agency_store = AgencyStore()
        self._owns_memory_review_store = memory_review_store is None
        self._owns_feedback_store = feedback_store is None
        self._owns_humanization_store = humanization_store is None
        self._owns_agency_store = agency_store is None
        self.recorder = recorder or PlayerLoopRecorder()
        self.log = logger
        self.now = now

        self.event_queue: queue.Queue[PlayerEvent] = queue.Queue()
        self.running = False
        self._next_screen_poll_at = 0.0
        self._seen_event_ids: set[str] = set()
        self.last_decision: Decision | None = None
        self.last_user_decision: UserDecisionRequest | None = None
        self._last_idle_log_at = 0.0
        self._seen_social_source_keys: set[str] = set()
        self._screen_observed_this_tick = False
        self._reported_ui_effect_outcomes: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------------
    # Lifecycle and event ingress
    # ------------------------------------------------------------------

    def set_runtime_availability(
        self,
        *,
        adb_connected: bool,
        ai_available: bool,
    ) -> None:
        self.world_state.adb_connected = bool(adb_connected)
        self.world_state.ai_available = bool(ai_available)

    def start(self) -> None:
        if self.running:
            return
        if self.capture is not None:
            starter = getattr(self.capture, "start", None)
            if callable(starter):
                try:
                    starter(clear_logcat=self.config.clear_logcat)
                except TypeError:
                    starter()
            ready = getattr(self.capture, "is_ready", None)
            if ready is False:
                raise RuntimeError("Chat capture did not become ready.")
            self.log("[CAPTURE] ready: private-chat listener is active")
        if self.config.enable_terminal_decision_control:
            if self.terminal_decision_control is None:
                self.terminal_decision_control = TerminalDecisionControl(self.user_decision_manager)
            self.terminal_decision_control.start()
            self.log("[USER DECISION] control: decision list | decision retry <id> | decision cancel <id> | decision assume-sent <id>")
        self.running = True
        self.publish(
            PlayerEvent(
                event_type=EventType.SYSTEM_START,
                source="player_loop",
                payload={
                    "capture_running": self.capture is not None,
                    "adb_connected": self.world_state.adb_connected,
                    "ai_available": self.world_state.ai_available,
                },
            )
        )
        self._drain_events()
        self.log("[EVENT] SYSTEM_START")

    def stop(self) -> None:
        if self.capture is not None:
            stopper = getattr(self.capture, "stop", None)
            if callable(stopper):
                stopper()
        self.running = False
        if self.terminal_decision_control is not None:
            self.terminal_decision_control.stop()
        self.publish(
            PlayerEvent(event_type=EventType.SYSTEM_STOP, source="player_loop")
        )
        self._drain_events()
        for store, owned in (
            (self.memory_review_store, self._owns_memory_review_store),
            (self.feedback_store, self._owns_feedback_store),
            (self.humanization_store, self._owns_humanization_store),
            (self.agency_store, self._owns_agency_store),
        ):
            closer = getattr(store, "close", None)
            if owned and callable(closer):
                closer()
        self.log("[EVENT] SYSTEM_STOP")

    def publish(self, event: PlayerEvent) -> None:
        if not isinstance(event, PlayerEvent):
            raise TypeError("event must be a PlayerEvent")
        self.event_queue.put(event)
        if self.config.record_events:
            self.recorder.record("events", event.to_dict())

    def submit_chat_message(self, message: Any) -> None:
        self.publish(PlayerEvent.from_chat_message(message))

    # ------------------------------------------------------------------
    # One bounded iteration; ideal for unit tests and the live loop.
    # ------------------------------------------------------------------

    def run_once(self) -> Decision:
        self._process_terminal_decision_commands()
        self._collect_capture_messages()
        self._screen_observed_this_tick = self._poll_screen_if_due()
        self.publish(PlayerEvent.tick())
        self._drain_events()
        self._consider_proactive_initiative()

        goal = self.goal_manager.get_next_goal(now=self.now())
        if goal is None:
            waiting_goal = self.goal_manager.get_waiting_goal()
            if waiting_goal is not None:
                decision = Decision(
                    DecisionType.WAIT,
                    waiting_goal.waiting_reason or "Navigation is awaiting observation.",
                    goal_id=waiting_goal.goal_id,
                    wait_state="WAITING_FOR_OBSERVATION",
                )
                self.last_decision = decision
                self._record_decision(decision)
                return decision
        if goal is not None:
            goal = self.goal_manager.activate_goal(goal.goal_id)
            self.world_state.current_goal_id = goal.goal_id

        context: dict[str, Any] = {
            "auto_send": self.config.auto_send,
            "send_lane_blocked": self.user_decision_manager.pending_delivery() is not None,
        }
        if goal is not None:
            pending_reply_id = goal.payload.get("pending_reply_id")
            if pending_reply_id:
                context["pending_reply_id"] = pending_reply_id
                context["target_uid"] = goal.payload.get("sender_uid")
                context["room_id"] = goal.payload.get("room_id")

        decision = self.decision_engine.decide(self.world_state, goal, context)
        self.last_decision = decision
        self._record_decision(decision)
        self._execute_decision(decision, goal)
        self._drain_events()
        return decision

    def run(self, *, max_iterations: int | None = None) -> None:
        self.start()
        iterations = 0
        try:
            while self.running:
                self.run_once()
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
                delay = max(0.05, float(self.config.tick_interval))
                if self._navigation_observation_pending():
                    delay = min(delay, max(0.05, float(self.config.ui_effect_poll_interval)))
                time.sleep(delay)
        except KeyboardInterrupt:
            self.log("[PLAYER LOOP] Stopped by user.")
        finally:
            self.stop()

    # ------------------------------------------------------------------
    # Event processing / state updates
    # ------------------------------------------------------------------

    def _collect_capture_messages(self) -> None:
        if self.capture is None:
            return
        getter = getattr(self.capture, "get_message", None)
        if not callable(getter):
            return
        for _ in range(max(1, int(self.config.max_events_per_tick))):
            try:
                message = getter(timeout=0)
            except TypeError:
                message = getter()
            if message is None:
                break
            self.submit_chat_message(message)

    def _poll_screen_if_due(self, *, force: bool = False) -> bool:
        if self.screen_provider is None:
            return False
        now = self.now()
        if not force and now < self._next_screen_poll_at:
            return False
        self._next_screen_poll_at = now + max(0.1, float(self.config.screen_poll_interval))
        try:
            observed = self.screen_provider()
        except Exception as exc:
            self.log(f"[STATE] screen provider failed: {type(exc).__name__}: {exc}")
            return True
        if not isinstance(observed, Mapping):
            return True
        unread = observed.get("unread", {})
        unread = unread if isinstance(unread, Mapping) else {}
        screen = str(observed.get("screen") or "UNKNOWN")
        debug = observed.get("debug", {})
        debug = debug if isinstance(debug, Mapping) else {}
        confidence = observed.get("confidence", debug.get("screen_confidence"))
        alternate = observed.get("alternate", debug.get("screen_alternate"))
        chat_shell_evidence = observed.get(
            "chat_shell_evidence", debug.get("chat_shell_evidence")
        )
        self.log(
            "[OBSERVE] "
            f"screen={screen} confidence={_format_observation_confidence(confidence)} "
            f"alternate={alternate or '-'} "
            f"chat_shell={str(bool(chat_shell_evidence)).lower()}"
        )
        if (
            screen != self.world_state.screen
            or confidence != self.world_state.screen_confidence
            or str(alternate or "") != str(self.world_state.screen_alternate or "")
            or bool(chat_shell_evidence) != self.world_state.chat_shell_evidence
            or bool(unread.get("private")) != self.world_state.unread_private
            or bool(unread.get("alliance")) != self.world_state.unread_alliance
        ):
            self.publish(
                PlayerEvent(
                    event_type=EventType.SCREEN_CHANGED,
                    source="screen_provider",
                    payload={
                        "screen": screen,
                        "confidence": confidence,
                        "alternate": alternate,
                        "chat_shell_evidence": chat_shell_evidence,
                        "unread_private": bool(unread.get("private")),
                        "unread_alliance": bool(unread.get("alliance")),
                    },
                )
            )
        return True

    def _drain_events(self) -> None:
        processed = 0
        while processed < max(1, int(self.config.max_events_per_tick)):
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                return
            processed += 1
            if event.event_id in self._seen_event_ids:
                self.log(f"[EVENT] duplicate ignored id={event.event_id}")
                continue
            self._seen_event_ids.add(event.event_id)
            changed = self.world_state.apply_event(event)
            if event.event_type == EventType.CHAT_MESSAGE:
                self._handle_chat_event(event)
            elif event.event_type == EventType.SCREEN_CHANGED and changed:
                self.log(
                    "[STATE] "
                    f"screen={self.world_state.screen} "
                    f"private_unread={self.world_state.unread_private} "
                    f"alliance_unread={self.world_state.unread_alliance}"
                )
                self._resume_safe_navigation_blocks_on_screen_change()
            elif event.event_type in {EventType.ACTION_COMPLETED, EventType.ACTION_FAILED}:
                self.log(
                    f"[RESULT] action={event.payload.get('action')} "
                    f"status={event.payload.get('status')}"
                )

    def _handle_chat_event(self, event: PlayerEvent) -> None:
        message = event.payload.get("message")
        if not isinstance(message, Mapping):
            self.log("[EVENT] CHAT_MESSAGE ignored: invalid payload")
            return
        room_id = str(message.get("room_id") or "")
        sender_uid = str(message.get("sender_uid") or "")
        group = str(message.get("group") or "").lower()
        if not room_id or not sender_uid or group not in {"custom", "alliance"}:
            self.log("[EVENT] CHAT_MESSAGE ignored: unsupported social payload")
            return
        payload = dict(message)
        if group == "custom":
            self.log(
                incoming_message(
                    sender=payload.get("sender_name"),
                    uid=sender_uid,
                    room_id=room_id,
                    seq_id=payload.get("seq_id"),
                    text=payload.get("text"),
                )
            )
        source_key = str(payload.get("source_message_key") or "")
        duplicate = bool(source_key and source_key in self._seen_social_source_keys)
        if source_key:
            self._seen_social_source_keys.add(source_key)
        profile = self._observe_social_profile(payload, duplicate=duplicate)
        human_profile = None
        if group == "custom" and not duplicate and self.humanization_store is not None:
            try:
                human_profile = self.humanization_store.observe_incoming(
                    player_uid=sender_uid,
                    room_id=room_id,
                    text=str(payload.get("text") or ""),
                    at=event.created_at,
                    relationship=str(getattr(getattr(profile, "relationship", None), "value", "") or "") or None,
                )
                suppressed, indefinite = negative_social_signal(str(payload.get("text") or ""))
                if suppressed:
                    self.humanization_store.set_proactive_suppression(
                        sender_uid,
                        indefinite=indefinite,
                        until=None if indefinite else self.now() + 6 * 3600,
                    )
                    self.log(
                        f"[PROACTIVE SKIP] uid={sender_uid} reason="
                        f"{'explicit_opt_out' if indefinite else 'negative_social_signal'}"
                    )
                self.log(
                    f"[HUMAN PROFILE] uid={sender_uid} relationship={human_profile.relationship} "
                    f"familiarity={human_profile.familiarity:.2f}"
                )
            except Exception as exc:
                self.log(f"[HUMAN PROFILE] uid={sender_uid} unavailable={type(exc).__name__}")
        private_context = None
        if group == "custom":
            private_context = self.private_contexts.get_or_create(
                player_uid=sender_uid,
                room_id=room_id,
            )
        analysis = self.intent_analyzer.analyze(
            payload,
            context={
                "duplicate": duplicate,
                "has_private_context": bool(private_context and private_context.recent_messages),
            },
            self_uid=self._own_uid(),
        )
        parsed_command = None
        pending_command = None
        reviewed_memory_candidate: dict[str, Any] | None = None
        if private_context is not None and not duplicate:
            private_context.add_user_message(payload, timestamp=event.created_at)
            private_context.apply_analysis(analysis)
            parsed_command = self.command_parser.parse(
                str(payload.get("text") or ""),
                sender_uid=sender_uid,
                sender_name=_optional_text(payload.get("sender_name")),
            )
            if parsed_command.is_actionable:
                pending_command = PendingCommandGoal.from_parsed(
                    parsed_command,
                    sender_uid=sender_uid,
                )
                waiting_until = (
                    self.now() + parsed_command.duration_seconds
                    if parsed_command.duration_seconds is not None
                    else None
                )
                private_context.set_command(parsed_command, waiting_until=waiting_until)
                self.world_state.pending_command_id = pending_command.command_id
                self.world_state.pending_command_type = pending_command.command_type
                self.world_state.pending_command_status = pending_command.status.value
        self._record_analysis(analysis, payload)
        if group == "alliance" and not duplicate:
            self.alliance_context.add_message(payload, timestamp=event.created_at)
        if not duplicate and analysis.intent.value != "IGNORE":
            candidate_memory = self.memory_candidate_detector.detect(str(payload.get("text") or ""))
            if candidate_memory is not None:
                review = self._review_memory_candidate(candidate_memory.to_dict(), payload)
                reviewed_memory_candidate = (
                    review.to_dict() if review is not None else candidate_memory.to_dict()
                )
                self._record_memory_candidate(
                    reviewed_memory_candidate,
                    payload,
                )
                if self.humanization_store is not None:
                    try:
                        self.humanization_store.create_memory_candidate(
                            player_uid=sender_uid,
                            memory_type=_human_memory_type(candidate_memory.memory_type),
                            content=candidate_memory.content,
                            source_message_key=_optional_text(payload.get("source_message_key")),
                            source_text=str(payload.get("text") or ""),
                            confidence=candidate_memory.confidence,
                            importance=min(0.9, max(0.3, candidate_memory.confidence)),
                            expires_at=candidate_memory.expires_at / 1000 if candidate_memory.expires_at else None,
                        )
                    except Exception as exc:
                        self.log(f"[MEMORY] candidate persistence failed: {type(exc).__name__}")
            # Legacy numeric style candidates remain historical/review data;
            # Agency mode must never turn a player's wording request into a
            # personality control input.
            style_change = _style_candidate_from_text(str(payload.get("text") or ""))
            if style_change and self.humanization_store is not None and not self.config.agency_enabled:
                try:
                    self.humanization_store.create_style_candidate(
                        player_uid=sender_uid,
                        changes=style_change,
                        source_text=str(payload.get("text") or ""),
                        confidence=0.78,
                    )
                    self.log(f"[STYLE] uid={sender_uid} candidate={','.join(sorted(style_change))}")
                except Exception as exc:
                    self.log(f"[STYLE] candidate persistence failed: {type(exc).__name__}")

        social = self.social_policy.evaluate(
            analysis,
            payload,
            self.alliance_context if group == "alliance" else None,
            self.world_state,
            profile,
        )
        self._record_social_decision(social, analysis, payload)
        self.log(f"[EVENT] CHAT_MESSAGE sender={sender_uid} seq={message.get('seq_id')}")
        if not social.should_reply:
            decision = Decision(DecisionType.IGNORE, social.reason)
            self._record_decision(decision)
            if group == "custom":
                self._record_private_training(
                    payload=payload,
                    analysis=analysis,
                    decision="IGNORE",
                    reply="",
                    command=parsed_command,
                    pending_command=pending_command,
                    memory_candidates=([reviewed_memory_candidate] if reviewed_memory_candidate else []),
                )
            return

        goal_type = (
            GoalType.ANSWER_PRIVATE_MESSAGE
            if group == "custom"
            else GoalType.ANSWER_ALLIANCE_MESSAGE
        )
        batch_window = (
            self.config.private_batch_window
            if group == "custom"
            else self.config.alliance_batch_window
        )
        now = self.now()
        response_due_at = now + max(0.0, float(batch_window))
        if group == "custom" and self.config.humanization_enabled and self.config.response_delay:
            delay = self.response_delay_manager.schedule(
                now=now,
                text=str(payload.get("text") or ""),
                urgent=bool(parsed_command and parsed_command.is_actionable),
            )
            response_due_at = max(response_due_at, delay.due_at)
            self.log(
                f"[REPLY DELAY] uid={sender_uid} category={delay.category} "
                f"seconds={delay.seconds:.2f} due_at={delay.due_at:.3f}"
            )
        actionable_command = parsed_command if parsed_command and parsed_command.is_actionable else None
        candidate = Goal(
            goal_type=goal_type,
            priority=GoalPriority(int(social.priority)),
            reason=social.reason,
            max_attempts=max(1, int(self.config.max_action_retries)),
            created_at=now,
            not_before=response_due_at,
            payload={
                "dedupe_key": f"{group}:{room_id}:{sender_uid}",
                "room_id": room_id,
                "sender_uid": sender_uid,
                "group": group,
                "messages": [payload],
                "analysis": analysis.to_dict(),
                "parsed_command": actionable_command.to_dict() if actionable_command else None,
                "pending_command": pending_command.to_dict() if pending_command else None,
                "parsed_commands": ([actionable_command.to_dict()] if actionable_command else []),
                "pending_commands": ([pending_command.to_dict()] if pending_command else []),
                "memory_candidates": ([reviewed_memory_candidate] if reviewed_memory_candidate else []),
                "scheduled_response_at": response_due_at,
            },
        )
        goal = self.goal_manager.add_goal(candidate)
        action = "merged" if goal.goal_id != candidate.goal_id else "created"
        self.log(
            f"[GOAL] {action} {goal.goal_type.value} "
            f"priority={goal.priority.name} id={goal.goal_id}"
        )

    def _own_uid(self) -> str | None:
        config = getattr(self.agent, "config", None)
        value = getattr(config, "own_uid", None)
        if value:
            return str(value)
        capture_config = getattr(self.capture, "config", None)
        value = getattr(capture_config, "self_uid", None)
        return str(value) if value else SELF_UID

    def _observe_social_profile(
        self,
        payload: Mapping[str, Any],
        *,
        duplicate: bool,
    ) -> Any | None:
        if duplicate:
            return None
        if str(payload.get("sender_uid") or "") == str(self._own_uid() or ""):
            return None
        store = getattr(self.agent, "store", None)
        observer = getattr(store, "observe_social_message", None)
        if not callable(observer):
            return None
        try:
            return observer(
                uid=str(payload.get("sender_uid") or ""),
                name=_optional_text(payload.get("sender_name")),
                alliance=_optional_text(payload.get("sender_alliance")),
                server_id=_optional_int(payload.get("sender_server_id")),
                group=str(payload.get("group") or ""),
                seen_at=_payload_timestamp_ms(payload, self.now()),
            )
        except Exception as exc:
            self.log(f"[SOCIAL] profile update failed: {type(exc).__name__}: {exc}")
            return None

    def _record_analysis(self, analysis: MessageAnalysis, payload: Mapping[str, Any]) -> None:
        record = {"message": str(payload.get("text") or ""), **analysis.to_dict()}
        if self.config.record_events:
            self.recorder.record("message_analysis", record)
        self.publish(PlayerEvent(EventType.MESSAGE_ANALYZED, "social", record))
        if self.config.social_debug:
            self.log(
                "[INTENT] "
                f"group={analysis.group} sender={analysis.sender_uid} "
                f"intent={analysis.intent.value} confidence={analysis.confidence:.2f} "
                f"directed={analysis.directed_at_peva} reason={analysis.reason}"
            )

    def _record_social_decision(
        self,
        social: SocialDecision,
        analysis: MessageAnalysis | Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        record = {
            "message": str(payload.get("text") or ""),
            "group": analysis.group,
            "sender_uid": analysis.sender_uid,
            "intent": analysis.intent.value,
            "directed_at_peva": analysis.directed_at_peva,
            "decision": social.action.upper(),
            "reason": social.reason,
        }
        if self.config.record_events:
            self.recorder.record("social_decisions", record)
        self.publish(PlayerEvent(EventType.SOCIAL_DECISION, "social_policy", record))
        if self.config.social_debug:
            self.log(
                f"[SOCIAL] {social.action.upper()} group={analysis.group} "
                f"sender={analysis.sender_uid} reason={social.reason}"
            )

    def _record_memory_candidate(self, candidate: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        record = {
            "sender_uid": str(payload.get("sender_uid") or ""),
            "group": str(payload.get("group") or ""),
            "message": str(payload.get("text") or ""),
            **dict(candidate),
        }
        if self.config.record_events:
            self.recorder.record("memory_candidates", record)
        self.publish(PlayerEvent(EventType.MEMORY_CANDIDATE, "memory_candidate_detector", record))
        if self.config.social_debug:
            self.log(f"[SOCIAL] MEMORY_CANDIDATE type={record.get('memory_type')}")

    def _review_memory_candidate(
        self,
        candidate: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> Any | None:
        """Promote only explicit identity statements; all else remains reviewable."""
        if str(payload.get("group") or "") != "custom":
            return None
        if self.memory_review_store is None:
            self.memory_review_store = MemoryReviewStore()
        try:
            return self.memory_review_store.ingest(
                player_uid=str(payload.get("sender_uid") or ""),
                memory_type=str(candidate.get("memory_type") or "other"),
                content=str(candidate.get("content") or ""),
                confidence=float(candidate.get("confidence") or 0.0),
                source_message_key=_optional_text(payload.get("source_message_key")),
                expires_at=_optional_int(candidate.get("expires_at")),
                conversation_store=getattr(self.agent, "store", None),
            )
        except Exception as exc:
            self.log(f"[SOCIAL] memory review failed: {type(exc).__name__}: {exc}")
            return None

    def _record_private_training(
        self,
        *,
        payload: Mapping[str, Any],
        analysis: MessageAnalysis | Mapping[str, Any],
        decision: str,
        reply: str,
        command: Any | None,
        pending_command: Any | None,
        recent_context: list[str] | None = None,
        memory_candidates: list[Mapping[str, Any]] | None = None,
        reply_original: str | None = None,
        reply_guard_triggered: bool = False,
        reply_rewritten: bool = False,
        reply_rewrite_source: str = "original",
        guard_reason: str | None = None,
    ) -> None:
        # Runtime stabilization must not turn live auto-send behaviour or a
        # failed delivery into training data. Operational event logs remain
        # available as debugging evidence when record_events is enabled.
        if self.config.auto_send:
            return
        if not self.config.record_events:
            return
        source_key = str(payload.get("source_message_key") or "")
        message_key = source_key or f"{payload.get('room_id')}:{payload.get('sender_uid')}:{int(self.now() * 1000)}"
        feedback_id = None
        if reply:
            if self.feedback_store is None:
                self.feedback_store = FeedbackStore()
            feedback = self.feedback_store.record(
                message_key=message_key,
                player_uid=str(payload.get("sender_uid") or ""),
                model_reply=reply,
                intent=_analysis_intent_value(analysis),
                decision=decision,
                command=_command_type_value(command),
                original_reply=reply_original,
                rewrite_source=reply_rewrite_source,
                guard_reason=guard_reason,
            )
            feedback_id = feedback.feedback_id
        self.recorder.record_raw_training({
            "message_key": message_key,
            "room_id": str(payload.get("room_id") or ""),
            "player_uid": str(payload.get("sender_uid") or ""),
            "channel": "custom",
            "input_message": str(payload.get("text") or ""),
            "recent_context": recent_context or [],
            "intent": _analysis_intent_value(analysis),
            "decision": decision,
            "command_type": _command_type_value(command),
            "command_status": _command_status_value(pending_command),
            "reply": reply,
            "reply_original": reply_original,
            "reply_guard_triggered": bool(reply_guard_triggered),
            "reply_rewritten": bool(reply_rewritten),
            "reply_rewrite_source": reply_rewrite_source,
            "guard_reason": guard_reason,
            "memory_candidates": [dict(item) for item in (memory_candidates or [])],
            "feedback_id": feedback_id,
            "timestamp": self.now(),
        })

    def _consider_proactive_initiative(self) -> None:
        """Let Python form a safe set; let Agency choose a person or nobody."""
        if not self.config.agency_enabled:
            self._consider_legacy_proactive_initiative()
            return
        if not self.config.proactive_enabled or self.humanization_store is None:
            return
        now = self.now()
        if now < getattr(self, "_next_proactive_check_at", 0.0):
            return
        self._next_proactive_check_at = now + 300.0
        if self.user_decision_manager.pending_delivery() is not None:
            self.log("[PROACTIVE SKIP] reason=send_lane_or_ambiguous_delivery")
            return
        if bool(self.world_state.game_busy):
            self.log("[PROACTIVE SKIP] reason=navigation_conflict")
            return
        if self.humanization_store.proactive_count_since(now - 3600) >= self.config.proactive_global_hourly_limit:
            self.log("[PROACTIVE SKIP] reason=global_hourly_limit")
            return
        if any(
            item.get("goal_type") in {GoalType.PROACTIVE_PRIVATE_MESSAGE.value, GoalType.ANSWER_PRIVATE_MESSAGE.value}
            and item.get("status") in {GoalStatus.PENDING.value, GoalStatus.ACTIVE.value}
            for item in self.goal_manager.snapshot()
        ):
            return
        candidates = self._safe_proactive_candidates(now)
        if not candidates:
            self.log("[PROACTIVE SKIP] reason=no_safe_candidate")
            return
        reflections = self._self_reflections()
        decision = self.agency_core.decide_proactive(
            self.agency_core.proactive_context(candidates=candidates, recent_context=[], self_reflections=reflections),
            candidate_uids={str(item["uid"]) for item in candidates},
        )
        if self.agency_store is not None:
            self.agency_store.record_proactive(
                goal_id=None,
                target_uid=decision.target_uid,
                room_id=next((str(item["room_id"]) for item in candidates if item["uid"] == decision.target_uid), None),
                decision=decision.to_dict(),
                candidate_uids=[str(item["uid"]) for item in candidates],
                status="SELECTED" if decision.decision == ProactiveDecision.START_CONVERSATION.value else "NO_ACTION",
                provenance={"source": "agency_core", "candidate_set": "python_safe"},
            )
        if decision.decision == ProactiveDecision.DO_NOTHING.value:
            self.log(f"[PROACTIVE SKIP] reason={decision.reason_code}")
            return
        selected = next((item for item in candidates if str(item["uid"]) == decision.target_uid), None)
        if selected is None:  # defensive containment even after schema validation
            self.log("[PROACTIVE SKIP] reason=target_not_in_safe_candidate_set")
            return
        self.goal_manager.add_goal(Goal(
            goal_type=GoalType.PROACTIVE_PRIVATE_MESSAGE,
            priority=GoalPriority.BACKGROUND,
            reason=decision.reason_code,
            created_at=now,
            payload={
                "dedupe_key": f"proactive:{selected['uid']}",
                "sender_uid": str(selected["uid"]),
                "room_id": str(selected["room_id"]),
                "agency_decision": decision.to_dict(),
                "proactive_candidate": dict(selected),
            },
        ))

    def _safe_proactive_candidates(self, now: float) -> list[dict[str, Any]]:
        """Deterministic eligibility only: no personality score, probability, or social energy."""
        if self.humanization_store is None:
            return []
        result: list[dict[str, Any]] = []
        for item in self.humanization_store.list_social_candidates():
            uid, room_id = str(item.get("player_uid") or ""), str(item.get("last_room_id") or "")
            if not uid or not room_id or not is_direct_private_room(room_id, self_uid=self._own_uid(), peer_uid=uid):
                continue
            if bool(item.get("proactive_suppressed_indefinitely")):
                continue
            until = _optional_float(item.get("proactive_suppressed_until"))
            if until is not None and until > now:
                continue
            last = _optional_float(item.get("last_proactive_at"))
            if last is not None and now - last < self.config.proactive_same_player_min_minutes * 60:
                continue
            summary = _optional_text(item.get("conversation_summary"))
            context = "has an existing verified private conversation"
            if summary:
                context += "; " + summary[:240]
            result.append({"uid": uid, "room_id": room_id, "context": context})
        return sorted(result, key=lambda item: (str(item["uid"]), str(item["room_id"])))[:8]

    def _self_reflections(self) -> list[str]:
        if self.agency_store is None:
            return []
        try:
            return [str(item["content"]) for item in self.agency_store.list_self_reflections(limit=4)]
        except Exception:
            return []

    def _consider_legacy_proactive_initiative(self) -> None:
        """Queue at most a Python-selected proactive candidate, never a UI action."""
        if not self.config.proactive_enabled or self.humanization_store is None:
            return
        now = self.now()
        next_check = getattr(self, "_next_proactive_check_at", 0.0)
        if now < next_check:
            return
        self._next_proactive_check_at = now + 300.0
        if any(
            item.get("goal_type") in {
                GoalType.PROACTIVE_PRIVATE_MESSAGE.value,
                GoalType.ANSWER_PRIVATE_MESSAGE.value,
            }
            and item.get("status") in {GoalStatus.PENDING.value, GoalStatus.ACTIVE.value}
            for item in self.goal_manager.snapshot()
        ):
            return
        try:
            decision = self.social_initiative_manager.choose(
                self.humanization_store.list_social_candidates(),
                proactive_last_hour=self.humanization_store.proactive_count_since(now - 3600),
                send_lane_busy=self.user_decision_manager.pending_delivery() is not None,
                ambiguous_delivery=self.user_decision_manager.pending_delivery() is not None,
                navigation_conflict=bool(self.world_state.game_busy),
                at=now,
            )
        except Exception as exc:
            self.log(f"[PROACTIVE CHECK] result=skip reason={type(exc).__name__}")
            return
        if decision.candidate is None:
            self.log(f"[PROACTIVE SKIP] reason={decision.reason}")
            return
        item = decision.candidate
        self.log(
            f"[PROACTIVE CHECK] uid={item.player_uid} type={item.initiative_type.value} "
            f"score={item.score:.2f} result=eligible"
        )
        self.goal_manager.add_goal(Goal(
            goal_type=GoalType.PROACTIVE_PRIVATE_MESSAGE,
            priority=GoalPriority.BACKGROUND,
            reason=item.reason,
            created_at=now,
            payload={
                "dedupe_key": f"proactive:{item.player_uid}",
                "sender_uid": item.player_uid,
                "room_id": item.room_id,
                "proactive_candidate": {
                    "initiative_type": item.initiative_type.value,
                    "reason": item.reason,
                    "score": item.score,
                    "topic": item.topic,
                },
            },
        ))

    def _generate_proactive_reply(self, goal: Goal) -> None:
        """Generate text after Python eligibility; delivery reuses the PM lane."""
        if self.config.agency_enabled and all(callable(getattr(self.agent, name, None)) for name in ("record_incoming_message", "create_pending_reply")):
            self._generate_agency_proactive_reply(goal)
            return
        if self.humanization_store is None:
            self.goal_manager.complete_goal(goal.goal_id, reason="humanization_store_unavailable")
            return
        data = goal.payload.get("proactive_candidate")
        if not isinstance(data, Mapping):
            self.goal_manager.complete_goal(goal.goal_id, reason="missing_proactive_candidate")
            return
        uid = str(goal.payload.get("sender_uid") or "")
        room_id = str(goal.payload.get("room_id") or "")
        complete = getattr(getattr(self.agent, "ai", None), "complete_task_safe", None)
        if not uid or not room_id or not callable(complete):
            self.log(f"[PROACTIVE SKIP] uid={uid or '-'} reason=generator_unavailable")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_generator_unavailable")
            return
        try:
            profile = self.humanization_store.get_profile(uid)
            style = self.humanization_store.get_style(uid)
            memories = self.humanization_store.get_memories(uid, limit=3)
            result = complete(
                system_prompt=(
                    "Write only one short, casual Last Z private-chat message. "
                    "Do not mention being an AI or memory. Do not claim any game action, completion, battle, march, or UI result."
                ),
                user_prompt=(
                    f"Initiative type: {data.get('initiative_type')}\nReason: {data.get('reason')}\n"
                    f"Relationship: {profile.relationship}; familiarity={profile.familiarity:.2f}\n"
                    f"Style: softness={style.softness:.2f}, playfulness={style.playfulness:.2f}, emoji={style.emoji_level}\n"
                    + ("Relevant memory: " + "; ".join(item.content for item in memories) if memories else "")
                ),
            )
        except Exception as exc:
            self.log(f"[PROACTIVE SKIP] uid={uid} reason=generation_error:{type(exc).__name__}")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_generation_error")
            return
        raw = str(getattr(result, "text", "") or "").strip()
        if getattr(result, "error", None) or not raw:
            self.log(f"[PROACTIVE SKIP] uid={uid} reason=empty_generation")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_empty_generation")
            return
        message_payload = {"text": str(data.get("reason") or ""), "source_message_key": f"proactive:{goal.goal_id}"}
        safe = self._safe_private_reply(raw, goal, message_payload)
        quality = self.reply_quality_gate.validate(safe.final_reply)
        if not quality.accepted:
            self.log(f"[PROACTIVE SKIP] uid={uid} reason=quality:{quality.reason}")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_quality_rejected")
            return
        natural = self.reply_naturalness.evaluate(
            safe.final_reply,
            recent_replies=self.humanization_store.recent_replies(uid, limit=8),
        )
        self.log(f"[NATURALNESS] uid={uid} verdict={natural.verdict.value} reason={natural.reason}")
        if natural.verdict != NaturalnessVerdict.PASS:
            self.log(f"[PROACTIVE SKIP] uid={uid} reason=naturalness:{natural.reason}")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_naturalness_rejected")
            return
        auto_send = bool(self.config.auto_send and self.config.proactive_auto_send)
        proactive_id = self.humanization_store.record_proactive(
            player_uid=uid,
            room_id=room_id,
            initiative_type=str(data.get("initiative_type") or "CHECK_IN"),
            reason=str(data.get("reason") or ""),
            topic=_optional_text(data.get("topic")),
            score=float(data.get("score") or 0.0),
            text=safe.final_reply,
            status="QUEUED" if auto_send else "CANDIDATE",
        )
        self.social_initiative_manager.note_proactive(at=self.now())
        goal.payload["proactive_id"] = proactive_id
        goal.payload["proactive"] = True
        self.log(
            f"[PROACTIVE CANDIDATE] uid={uid} type={data.get('initiative_type')} "
            f"score={float(data.get('score') or 0.0):.2f} text={safe.final_reply!r}"
        )
        if not auto_send:
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_preview_candidate")
            return
        queue_reply = getattr(self.agent, "queue_proactive_reply", None)
        if not callable(queue_reply):
            self.log(f"[PROACTIVE SKIP] uid={uid} reason=existing_send_queue_unavailable")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_queue_unavailable")
            return
        try:
            pending = queue_reply(player_uid=uid, room_id=room_id, text=safe.final_reply)
        except Exception as exc:
            self.log(f"[PROACTIVE SKIP] uid={uid} reason=queue_rejected:{type(exc).__name__}")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_queue_rejected")
            return
        goal.payload["pending_reply_id"] = str(getattr(pending, "reply_id", ""))
        goal.payload["pending_reply_text"] = safe.final_reply
        follow_up = self.decision_engine.decide(
            self.world_state, goal,
            {"auto_send": True, "pending_reply_id": goal.payload["pending_reply_id"], "target_uid": uid, "room_id": room_id},
        )
        self._record_decision(follow_up)
        self._execute_decision(follow_up, goal)

    def _generate_agency_proactive_reply(self, goal: Goal) -> None:
        data = goal.payload.get("agency_decision")
        uid, room_id = str(goal.payload.get("sender_uid") or ""), str(goal.payload.get("room_id") or "")
        if not isinstance(data, Mapping) or str(data.get("decision") or "") != ProactiveDecision.START_CONVERSATION.value:
            self.goal_manager.complete_goal(goal.goal_id, reason="missing_agency_start_decision")
            return
        raw = str(data.get("message") or "").strip()
        if not uid or not room_id or not raw or str(data.get("target_uid") or "") != uid:
            self.goal_manager.complete_goal(goal.goal_id, reason="invalid_agency_proactive_payload")
            return
        message_payload = {"text": "", "source_message_key": f"proactive:{goal.goal_id}"}
        safe = self._safe_private_reply(raw, goal, message_payload)
        quality = self.reply_quality_gate.validate(safe.final_reply)
        final_text = safe.final_reply if quality.accepted else self.reply_quality_gate.fallback()
        if not quality.accepted:
            final_text = self._safe_private_reply(final_text, goal, message_payload).final_reply
        natural = self.reply_naturalness.evaluate(final_text)
        self.log(f"[NATURALNESS] uid={uid} verdict={natural.verdict.value} reason={natural.reason}")
        auto_send = bool(self.config.auto_send and self.config.proactive_auto_send)
        proactive_id = ""
        if self.humanization_store is not None:
            proactive_id = self.humanization_store.record_proactive(
                player_uid=uid, room_id=room_id, initiative_type="AGENCY", reason=str(data.get("reason_code") or ""),
                topic=None, score=0.0, text=final_text, status="QUEUED" if auto_send else "CANDIDATE",
            )
        goal.payload["proactive_id"], goal.payload["proactive"] = proactive_id, True
        self.log(f"[PROACTIVE CANDIDATE] uid={uid} decision=START_CONVERSATION reason={data.get('reason_code')} text={final_text!r}")
        if not auto_send:
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_preview_candidate")
            return
        queue_reply = getattr(self.agent, "queue_proactive_reply", None)
        if not callable(queue_reply):
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_queue_unavailable")
            return
        try:
            pending = queue_reply(player_uid=uid, room_id=room_id, text=final_text)
        except Exception as exc:
            self.log(f"[PROACTIVE SKIP] uid={uid} reason=queue_rejected:{type(exc).__name__}")
            self.goal_manager.complete_goal(goal.goal_id, reason="proactive_queue_rejected")
            return
        goal.payload["pending_reply_id"], goal.payload["pending_reply_text"] = str(getattr(pending, "reply_id", "")), final_text
        follow_up = self.decision_engine.decide(self.world_state, goal, {"auto_send": True, "pending_reply_id": goal.payload["pending_reply_id"], "target_uid": uid, "room_id": room_id})
        self._record_decision(follow_up)
        self._execute_decision(follow_up, goal)

    # ------------------------------------------------------------------
    # Decisions and actions
    # ------------------------------------------------------------------

    def _record_decision(self, decision: Decision) -> None:
        is_idle_wait = (
            decision.decision_type == DecisionType.WAIT
            and decision.goal_id is None
        )
        now = self.now()
        if is_idle_wait and now - self._last_idle_log_at < 30.0:
            return
        if is_idle_wait:
            self._last_idle_log_at = now
        self.log(
            f"[DECISION] {decision.decision_type.value} "
            f"decision={decision.decision_id} goal={decision.goal_id or '-'} "
            f"reason={decision.reason}"
        )
        if self.config.record_events:
            self.recorder.record("decisions", decision.to_dict())

    def _execute_decision(self, decision: Decision, goal: Goal | None) -> None:
        if decision.decision_type in {DecisionType.NO_OP, DecisionType.IGNORE}:
            return
        if decision.decision_type == DecisionType.WAIT:
            self._handle_wait(decision, goal)
            return
        if decision.decision_type == DecisionType.ASK_USER:
            # Legacy decision type is intentionally inert. New requests are
            # created only by an AMBIGUOUS delivery outcome, exactly once.
            self._handle_wait(decision, goal)
            return
        if goal is None:
            return
        if decision.decision_type == DecisionType.GENERATE_REPLY:
            if goal.goal_type == GoalType.PROACTIVE_PRIVATE_MESSAGE:
                self._generate_proactive_reply(goal)
                return
            self._generate_reply(goal)
            return
        if decision.decision_type == DecisionType.NAVIGATE_PRIVATE_REPLY:
            self._advance_private_reply_navigation(goal)
            return
        if decision.decision_type == DecisionType.SEND_PRIVATE_REPLY:
            self._send_pending_reply(goal, decision)
            return
        if decision.action:
            if bool(decision.action.get("private_reply_navigation")):
                # Compatibility for persisted/older decisions.  Routing is
                # centralized in NavigationManager; never revive the former
                # action-local private-navigation state machine.
                self._advance_private_reply_navigation(goal)
                return
            self._execute_action(goal, decision.action)

    def _generate_reply(self, goal: Goal) -> None:
        message_payload = _batched_goal_message(goal)
        if message_payload is None:
            self.goal_manager.complete_goal(goal.goal_id, reason="No remaining message payload.")
            return
        group = str(message_payload.get("group") or goal.payload.get("group") or "custom")
        try:
            message = _chat_message_from_payload(message_payload)
            if group == "alliance":
                reply = self._generate_alliance_reply(message, goal)
                if getattr(reply, "error", None):
                    self._fail_goal(goal, f"alliance_ai_error:{getattr(reply, 'error')}")
                    return
                if not str(getattr(reply, "text", "") or "").strip():
                    self._consume_goal_message(goal, completed_reason="empty_alliance_preview", consume_all=True)
                    return
                self.log(f"[AI] {getattr(reply, 'text', '')}")
                self._note_reply_candidate(goal)
                self.log("[PREVIEW] Alliance reply generated; alliance sending is disabled.")
                self._consume_goal_message(goal, completed_reason="alliance_preview_reply_ready", consume_all=True)
                return
            if self.config.agency_enabled and all(callable(getattr(self.agent, name, None)) for name in ("record_incoming_message", "create_pending_reply")):
                self._generate_agency_private_reply(goal, message_payload, message)
                return
            social_context = self._reply_context_for(goal, channel="private")
            try:
                result = self.agent.process_chat_message(message, social_context=social_context)
            except TypeError as exc:
                if "social_context" not in str(exc):
                    raise
                result = self.agent.process_chat_message(message)
        except Exception as exc:
            self._fail_goal(goal, f"agent_error:{type(exc).__name__}: {exc}")
            return

        status = str(getattr(result, "status", "unknown"))
        self.log(f"[RESULT] reply_generation={status}")
        if not bool(getattr(result, "reply_ready", False)):
            if status in {"duplicate", "ignored", "blocked"}:
                self._consume_goal_message(goal, completed_reason=status, consume_all=True)
            else:
                self._fail_goal(goal, f"reply_generation:{status}")
            return

        pending = getattr(result, "pending_reply", None)
        if pending is None:
            self._fail_goal(goal, "reply_ready_without_pending_reply")
            return
        reply_id = str(getattr(pending, "reply_id", ""))
        if not reply_id:
            self._fail_goal(goal, "pending_reply_without_id")
            return

        reply_safety = self._safe_private_reply(
            str(getattr(pending, "text", "") or ""),
            goal,
            message_payload,
        )
        safe_reply = reply_safety.final_reply
        quality = self.reply_quality_gate.validate(safe_reply)
        quality_fallback = False
        if not quality.accepted:
            quality_fallback = True
            self.log(f"[QUALITY] rejected reason={quality.reason}; using safe fallback.")
            safe_reply = self.reply_quality_gate.fallback()
        humanization_rewritten = False
        humanization_source = reply_safety.rewrite_source
        if self.config.humanization_enabled and not self.config.agency_enabled:
            rewritten, source = self._humanize_reply(
                safe_reply,
                goal=goal,
                message_payload=message_payload,
            )
            if rewritten != safe_reply:
                humanization_rewritten = True
                # Action safety is deliberately re-run after a humanization
                # rewrite.  The rewrite cannot gain authority to claim a game action.
                final_safety = self._safe_private_reply(rewritten, goal, message_payload)
                safe_reply = final_safety.final_reply
                humanization_source = (
                    final_safety.rewrite_source
                    if final_safety.rewritten
                    else source
                )
                final_quality = self.reply_quality_gate.validate(safe_reply)
                if not final_quality.accepted:
                    quality_fallback = True
                    safe_reply = self.reply_quality_gate.fallback()
        if safe_reply != str(getattr(pending, "text", "") or ""):
            replacer = getattr(self.agent, "replace_reply_text", None)
            if callable(replacer):
                replacer(reply_id, safe_reply)
            pending.text = safe_reply
        final_was_rewritten = safe_reply != reply_safety.original_reply
        if final_was_rewritten or quality_fallback:
            self.log(ai_original(text=reply_safety.original_reply))
            self.log(
                ai_final(
                    source=("quality_gate_fallback" if quality_fallback else humanization_source),
                    text=safe_reply,
                )
            )
        self.log(
            ai_reply(
                goal_id=goal.goal_id,
                reply_id=reply_id,
                text=safe_reply,
            )
        )
        private_context = self.private_contexts.get(
            player_uid=str(goal.payload.get("sender_uid") or ""),
            room_id=str(goal.payload.get("room_id") or ""),
        )
        if private_context is not None:
            private_context.add_peva_reply(safe_reply, timestamp=self.now())
        if self.humanization_store is not None:
            try:
                self.humanization_store.record_reply_review(
                    player_uid=str(goal.payload.get("sender_uid") or ""),
                    room_id=str(goal.payload.get("room_id") or ""),
                    incoming_text=str(message_payload.get("text") or ""),
                    reply_text=safe_reply,
                    original_reply=reply_safety.original_reply,
                    context=(
                        private_context.render_relevant(limit=12, exclude_last=len(goal.payload.get("messages") or []) + 1)
                        if private_context is not None else []
                    ),
                )
            except Exception as exc:
                self.log(f"[HUMAN REVIEW] reply persistence failed: {type(exc).__name__}")
        self._record_private_training(
            payload=message_payload,
            analysis=goal.payload.get("analysis") if isinstance(goal.payload.get("analysis"), Mapping) else {},
            decision="REPLY",
            reply=safe_reply,
            command=goal.payload.get("parsed_command"),
            pending_command=goal.payload.get("pending_command"),
            reply_original=reply_safety.original_reply,
            reply_guard_triggered=reply_safety.guard_triggered,
            reply_rewritten=final_was_rewritten or humanization_rewritten,
            reply_rewrite_source=humanization_source,
            guard_reason=reply_safety.guard_reason,
            memory_candidates=(
                goal.payload.get("memory_candidates")
                if isinstance(goal.payload.get("memory_candidates"), list) else []
            ),
            recent_context=(
                private_context.render_relevant(
                    limit=12,
                    exclude_last=len(goal.payload.get("messages") or []) + 1,
                ) if private_context is not None else []
            ),
        )
        self._note_reply_candidate(goal)
        if not self.config.auto_send:
            discard = getattr(self.agent, "discard_reply", None)
            if callable(discard):
                discard(reply_id)
            self.log("[PREVIEW] Reply generated; nothing was sent to Last Z.")
            self._consume_goal_message(goal, completed_reason="preview_reply_ready", consume_all=True)
            return

        goal.payload["pending_reply_id"] = reply_id
        # Keep the final, guarded text with the parent navigation task so an
        # in-flight UI transition remains fully traceable in a snapshot.
        goal.payload["pending_reply_text"] = safe_reply
        goal.payload["pending_reply_source_keys"] = _message_source_keys(goal.payload.get("messages"))
        follow_up = self.decision_engine.decide(
            self.world_state,
            goal,
            {
                "auto_send": True,
                "pending_reply_id": reply_id,
                "target_uid": goal.payload.get("sender_uid"),
                "room_id": goal.payload.get("room_id"),
            },
        )
        self._record_decision(follow_up)
        self._execute_decision(follow_up, goal)

    def _generate_agency_private_reply(self, goal: Goal, message_payload: Mapping[str, Any], message: Any) -> None:
        """Use the structured Agency decision before creating any pending reply."""
        uid, room_id = str(goal.payload.get("sender_uid") or ""), str(goal.payload.get("room_id") or "")
        recent_context = self._agency_recent_context(uid, room_id, goal)
        memories = self._agency_player_memories(uid, room_id)
        decision = self.agency_core.decide_reactive(
            self.agency_core.reactive_context(
                incoming=message_payload,
                player_memories=memories,
                recent_context=recent_context,
                confirmed_facts=[str(item) for item in goal.payload.get("confirmed_actions", []) if str(item).strip()],
                self_reflections=self._self_reflections(),
            )
        )
        decision = self._preview_social_reply_override(
            decision=decision,
            analysis=goal.payload.get("analysis") if isinstance(goal.payload.get("analysis"), Mapping) else {},
            message=message,
            recent_context=recent_context,
        )
        self.log(f"[AGENCY] goal={goal.goal_id} decision={decision.decision} reason={decision.reason_code}")
        if decision.decision not in REACTIVE_WITH_MESSAGE:
            status = self._record_agency_incoming(message, goal, decision, status="NO_SEND")
            if status == "duplicate":
                self._consume_goal_message(goal, completed_reason="duplicate", consume_all=True)
            else:
                self._record_private_training(
                    payload=message_payload,
                    analysis=goal.payload.get("analysis") if isinstance(goal.payload.get("analysis"), Mapping) else {},
                    decision=decision.decision,
                    reply="",
                    command=goal.payload.get("parsed_command"),
                    pending_command=goal.payload.get("pending_command"),
                    recent_context=recent_context,
                )
                self._consume_goal_message(goal, completed_reason=f"agency_{decision.decision.lower()}", consume_all=True)
            return

        original = str(decision.message or "")
        quality = self.reply_quality_gate.validate(original)
        candidate = original if quality.accepted else self.reply_quality_gate.fallback()
        if not quality.accepted:
            self.log(f"[QUALITY] Agency message rejected reason={quality.reason}; using safe fallback.")
        reply_safety = self._safe_private_reply(candidate, goal, message_payload)
        safe_reply = reply_safety.final_reply
        natural = self.reply_naturalness.evaluate(safe_reply)
        self.log(
            f"[NATURALNESS] uid={uid or '-'} verdict={natural.verdict.value} "
            f"reason={natural.reason}"
        )
        result = self._create_agency_pending(message, safe_reply)
        status = str(getattr(result, "status", "unknown"))
        if not bool(getattr(result, "reply_ready", False)):
            if status == "duplicate":
                self._consume_goal_message(goal, completed_reason="duplicate", consume_all=True)
            else:
                self._fail_goal(goal, f"agency_pending:{status}")
            return
        pending = getattr(result, "pending_reply", None)
        reply_id = str(getattr(pending, "reply_id", "") or "")
        if not reply_id:
            self._fail_goal(goal, "agency_pending_without_id")
            return
        final_was_rewritten = safe_reply != original
        if final_was_rewritten:
            self.log(ai_original(text=original))
            self.log(ai_final(source=reply_safety.rewrite_source if reply_safety.rewritten else "quality_gate_fallback", text=safe_reply))
        self.log(ai_reply(goal_id=goal.goal_id, reply_id=reply_id, text=safe_reply))
        if self.agency_store is not None:
            self.agency_store.record_reactive(
                goal_id=goal.goal_id, player_uid=uid, room_id=room_id, incoming_text=str(message_payload.get("text") or ""),
                decision=decision.to_dict(), status="PENDING_REPLY", provenance={"source": "agency_core", "quality_accepted": quality.accepted, "action_guard": reply_safety.guard_triggered},
            )
        private_context = self.private_contexts.get(player_uid=uid, room_id=room_id)
        if private_context is not None:
            private_context.add_peva_reply(safe_reply, timestamp=self.now())
        self._record_private_training(
            payload=message_payload,
            analysis=goal.payload.get("analysis") if isinstance(goal.payload.get("analysis"), Mapping) else {},
            decision=decision.decision,
            reply=safe_reply,
            command=goal.payload.get("parsed_command"),
            pending_command=goal.payload.get("pending_command"),
            reply_original=original,
            reply_guard_triggered=reply_safety.guard_triggered,
            reply_rewritten=final_was_rewritten,
            reply_rewrite_source=reply_safety.rewrite_source,
            guard_reason=reply_safety.guard_reason,
            recent_context=recent_context,
        )
        self._note_reply_candidate(goal)
        if not self.config.auto_send:
            discard = getattr(self.agent, "discard_reply", None)
            if callable(discard):
                discard(reply_id)
            self.log("[PREVIEW] Agency reply generated; nothing was sent to Last Z.")
            self._consume_goal_message(goal, completed_reason="agency_preview_reply_ready", consume_all=True)
            return
        goal.payload["pending_reply_id"] = reply_id
        goal.payload["pending_reply_text"] = safe_reply
        goal.payload["pending_reply_source_keys"] = _message_source_keys(goal.payload.get("messages"))
        follow_up = self.decision_engine.decide(self.world_state, goal, {"auto_send": True, "pending_reply_id": reply_id, "target_uid": uid, "room_id": room_id})
        self._record_decision(follow_up)
        self._execute_decision(follow_up, goal)

    def _preview_social_reply_override(
        self,
        *,
        decision: AgencyDecision,
        analysis: Mapping[str, Any],
        message: Any,
        recent_context: list[str],
    ) -> AgencyDecision:
        """Generate a safe preview when Agency silences a clear social bid.

        This deliberately cannot run in auto-send mode and it never handles
        game-action requests.  The generated text still passes the normal
        quality and action-claim safety gates in the caller.
        """
        if not self.config.agency_social_preview_fallback or self.config.auto_send:
            return decision
        if decision.decision not in {"IGNORE", "WAIT"}:
            return decision
        policy = self.private_social_reply_policy.evaluate(analysis, str(getattr(message, "text", "") or ""))
        if not policy.should_generate_preview:
            return decision
        generator = getattr(self.agent, "generate_social_reply", None)
        if not callable(generator):
            self.log(f"[SOCIAL PREVIEW] skipped reason={policy.reason}: generator_unavailable")
            return decision
        try:
            generated = generator(
                message,
                history=recent_context,
                extra_context=[
                    "MODE: private preview only; do not claim an executed game action.",
                    "Respond naturally to the social message. A reply will be shown locally and not sent.",
                ],
            )
            text = " ".join(str(getattr(generated, "text", generated) or "").split()).strip()
        except Exception as exc:
            self.log(f"[SOCIAL PREVIEW] skipped reason={policy.reason}: generator_error={type(exc).__name__}")
            return decision
        if not text:
            self.log(f"[SOCIAL PREVIEW] skipped reason={policy.reason}: empty_generator_output")
            return decision
        self.log(f"[SOCIAL PREVIEW] overriding {decision.decision} reason={policy.reason}")
        return AgencyDecision(
            mode="AGENCY_REACTIVE",
            decision="REPLY",
            reason_code="SOCIAL_PREVIEW_OVERRIDE",
            message=text,
        )

    def _record_agency_incoming(self, message: Any, goal: Goal, decision: AgencyDecision, *, status: str) -> str:
        recorder = getattr(self.agent, "record_incoming_message", None)
        if callable(recorder):
            result = recorder(message)
            result_status = str(getattr(result, "status", "unknown"))
        else:
            # Compatibility only for old test doubles. Production LastZAgent
            # implements record_incoming_message and never calls the legacy LLM here.
            result_status = "recorded"
        if result_status != "duplicate" and self.agency_store is not None:
            self.agency_store.record_reactive(
                goal_id=goal.goal_id, player_uid=str(goal.payload.get("sender_uid") or ""), room_id=str(goal.payload.get("room_id") or ""),
                incoming_text=str(getattr(message, "text", "") or ""), decision=decision.to_dict(), status=status,
                provenance={"source": "agency_core"},
            )
        return result_status

    def _create_agency_pending(self, message: Any, text: str) -> Any:
        creator = getattr(self.agent, "create_pending_reply", None)
        if callable(creator):
            return creator(message, text)
        # Compatibility for pre-Agency test doubles; not used by production.
        return self.agent.process_chat_message(message)

    def _agency_recent_context(self, uid: str, room_id: str, goal: Goal) -> list[str]:
        private_context = self.private_contexts.get(player_uid=uid, room_id=room_id)
        if private_context is None:
            return []
        return private_context.render_relevant(limit=12, exclude_last=len(goal.payload.get("messages") or []))

    def _agency_player_memories(self, uid: str, room_id: str) -> list[str]:
        result: list[str] = []
        if self.humanization_store is not None:
            try:
                result.extend(f"{item.memory_type}: {item.content}" for item in self.humanization_store.get_memories(uid, limit=5))
            except Exception:
                pass
        store = getattr(self.agent, "store", None)
        getter = getattr(store, "get_ai_context", None)
        if callable(getter):
            try:
                context = getter(player_uid=uid, room_id=room_id, recent_history_limit=1, memory_limit=8, min_memory_importance=0.25)
                result.extend(str(item) for item in getattr(context, "memories", []) if str(item).strip())
            except Exception:
                pass
        return list(dict.fromkeys(result))[:8]

    def _generate_alliance_reply(self, message: Any, goal: Goal) -> Any:
        generator = getattr(self.agent, "generate_social_reply", None)
        if not callable(generator):
            raise RuntimeError("LastZAgent.generate_social_reply is unavailable")
        recent = self.alliance_context.recent_messages(12)
        if recent:
            recent = recent[:-1]
        history = [
            {"role": "user", "content": f"{item.sender_name or item.sender_uid}: {item.text}"}
            for item in recent
        ]
        return generator(
            message,
            history=history,
            extra_context=self._reply_context_for(goal, channel="alliance"),
        )

    def _reply_context_for(self, goal: Goal, *, channel: str) -> list[str]:
        sender_uid = str(goal.payload.get("sender_uid") or "")
        profile = None
        getter = getattr(getattr(self.agent, "store", None), "get_player_profile", None)
        if callable(getter) and sender_uid:
            try:
                profile = getter(sender_uid)
            except Exception:
                profile = None
        player_name = getattr(profile, "name", None) or sender_uid or "unknown"
        relationship = getattr(getattr(profile, "relationship", None), "value", "UNKNOWN")
        identity = self.intent_analyzer.identity
        context = [
            f"CHANNEL: {channel}",
            f"PLAYER: {player_name}; relationship={relationship}",
            self.personality.prompt_line(),
            f"PEVA ROLE: {identity.get('role', 'alliance member')}",
            "CONFIRMED FACTS: No unobserved game action has been completed.",
            "UNKNOWN FACTS: march, reinforcement, teleport and battle status unless explicitly supplied above.",
            "EXECUTED ACTIONS: none confirmed in this runtime context.",
        ]
        if channel == "private":
            if self.config.humanization_enabled and not self.config.agency_enabled and self.humanization_store is not None and sender_uid:
                try:
                    human = self.humanization_store.get_profile(
                        sender_uid,
                        fallback_relationship=str(relationship),
                    )
                    style = self.humanization_store.get_style(sender_uid)
                    if self.config.relationship_adaptation:
                        context.append(
                            "RELATIONSHIP ADAPTATION: "
                            f"relationship={human.relationship}; familiarity={human.familiarity:.2f}; "
                            "adapt warmth naturally, without announcing these values."
                        )
                    context.append(
                        "STYLE ADAPTATION: "
                        f"softness={style.softness:.2f}; playfulness={style.playfulness:.2f}; "
                        f"teasing={style.teasing:.2f}; directness={style.directness:.2f}; "
                        f"formality={style.formality:.2f}; emoji={style.emoji_level}; "
                        f"length={style.reply_length}; tags={','.join(style.tags) or '-'}; "
                        "use subtly and do not mention the profile."
                    )
                    if self.config.memory_adaptation:
                        memories = self.humanization_store.get_memories(sender_uid, limit=5)
                        if memories:
                            context.append(
                                "RELEVANT PLAYER MEMORY (use only when natural; never say that you remember):\n"
                                + "\n".join(f"- {item.memory_type}: {item.content}" for item in memories)
                            )
                            self.log(
                                f"[MEMORY USED] uid={sender_uid} ids={','.join(item.memory_id for item in memories)}"
                            )
                    if human.conversation_summary:
                        context.append("OLDER SOCIAL SUMMARY: " + human.conversation_summary)
                except Exception as exc:
                    self.log(f"[HUMAN PROFILE] uid={sender_uid} context_unavailable={type(exc).__name__}")
            private_context = self.private_contexts.get(
                player_uid=sender_uid,
                room_id=str(goal.payload.get("room_id") or ""),
            )
            if private_context is not None:
                recent = private_context.render_relevant(
                    limit=12,
                    exclude_last=len(goal.payload.get("messages") or []),
                )
                if recent:
                    context.append("RECENT PRIVATE CONTEXT:\n" + "\n".join(recent))
                pending_commands = goal.payload.get("pending_commands")
                if not isinstance(pending_commands, list):
                    pending = goal.payload.get("pending_command")
                    pending_commands = [pending] if pending else []
                if pending_commands:
                    context.append("PENDING COMMANDS: " + json.dumps(pending_commands, ensure_ascii=False, sort_keys=True))
                batched = _batched_goal_message(goal)
                mode = self.player_style.choose_length_mode(
                    str(batched.get("text") or "") if batched else ""
                )
                context.append(self.player_style.context_instruction(
                    mode=mode,
                    relationship=str(relationship),
                ))
        if channel == "alliance":
            recent = self.alliance_context.render_recent(12)
            if recent:
                context.append("RECENT ALLIANCE CONTEXT:\n" + "\n".join(recent))
        return context

    def _humanize_reply(
        self,
        reply: str,
        *,
        goal: Goal,
        message_payload: Mapping[str, Any],
    ) -> tuple[str, str]:
        """At most one local rewrite for safe but assistant-like wording."""
        uid = str(goal.payload.get("sender_uid") or "")
        recent: list[str] = []
        if self.humanization_store is not None and uid:
            try:
                recent = self.humanization_store.recent_replies(uid, limit=8)
            except Exception:
                recent = []
        verdict = self.reply_naturalness.evaluate(reply, recent_replies=recent)
        self.log(
            f"[NATURALNESS] uid={uid or '-'} verdict={verdict.verdict.value} "
            f"reason={verdict.reason} similarity={verdict.similarity:.2f}"
        )
        if verdict.verdict == NaturalnessVerdict.PASS or not self.config.naturalness_rewrite:
            return reply, "original"
        if verdict.verdict == NaturalnessVerdict.REJECT:
            return reply, "naturalness_reject_no_rewrite"
        ai = getattr(self.agent, "ai", None)
        complete = getattr(ai, "complete_task_safe", None)
        normalizer = getattr(ai, "normalize_reply", None)
        if not callable(complete):
            return reply, "naturalness_rewrite_unavailable"
        relationship = self._relationship_for(uid)
        try:
            rewrite = complete(
                system_prompt=(
                    "Rewrite one Last Z private-chat reply. Return only the replacement text. "
                    "Use casual natural player chat, be concise, avoid customer-support phrasing, "
                    "and do not claim any game action, completion, march, battle, or UI result."
                ),
                user_prompt=(
                    f"Relationship: {relationship}\n"
                    f"Incoming: {str(message_payload.get('text') or '')}\n"
                    f"Reply to rewrite: {reply}"
                ),
            )
        except Exception:
            return reply, "naturalness_rewrite_error"
        text = str(getattr(rewrite, "text", "") or "").strip()
        if getattr(rewrite, "error", None) or not text:
            return reply, "naturalness_rewrite_unavailable"
        if callable(normalizer):
            text = str(normalizer(text) or "").strip()
        retry = self.reply_naturalness.evaluate(text, recent_replies=recent)
        self.log(
            f"[NATURALNESS] uid={uid or '-'} verdict={retry.verdict.value} "
            f"reason={retry.reason} pass=after_one_rewrite"
        )
        if retry.verdict != NaturalnessVerdict.PASS:
            return reply, "naturalness_rewrite_rejected"
        return text, "llm_humanization_rewrite"

    def _safe_private_reply(
        self,
        reply: str,
        goal: Goal,
        message_payload: Mapping[str, Any],
    ) -> PrivateReplySafety:
        original_reply = " ".join(str(reply or "").split()).strip()
        mode = self.player_style.choose_length_mode(str(message_payload.get("text") or ""))
        # Agency-selected conversational text is never restyled. The only
        # permitted transformation below is the final game-action safety path.
        styled = original_reply if self.config.agency_enabled else self.player_style.postprocess(original_reply, mode=mode)
        private_context = self.private_contexts.get(
            player_uid=str(goal.payload.get("sender_uid") or ""),
            room_id=str(goal.payload.get("room_id") or ""),
        )
        pending = _highest_risk_pending_command(goal.payload.get("parsed_commands"))
        if pending is None:
            pending = goal.payload.get("parsed_command")
        if private_context is not None:
            context_pending = _highest_risk_pending_command(private_context.pending_commands)
            if context_pending is not None:
                pending = context_pending
            elif pending is None:
                pending = private_context.active_command
        pending_goal = _pending_goal_for_command(goal.payload.get("pending_commands"), pending)
        if pending_goal is None:
            pending_goal = goal.payload.get("pending_command")
        confirmed_actions = goal.payload.get("confirmed_actions")
        if not isinstance(confirmed_actions, list):
            confirmed_actions = []
        guarded = self.action_claim_guard.validate(
            styled,
            pending_command=pending,
            confirmed_actions=confirmed_actions,
        )
        if not guarded.changed:
            return PrivateReplySafety(
                original_reply=original_reply,
                final_reply=styled,
                guard_triggered=False,
                rewritten=False,
                rewrite_source="original",
            )

        if self.config.social_debug:
            self.log(f"[SAFETY] ActionClaimGuard: {guarded.reason}; requesting safe rewrite.")
        relationship = self._relationship_for(str(goal.payload.get("sender_uid") or ""))
        recent_context = (
            private_context.render_relevant(
                limit=12,
                exclude_last=len(goal.payload.get("messages") or []),
            ) if private_context is not None else []
        )
        attempt = self.action_claim_rewriter.rewrite_unsafe_reply(
            ai=getattr(self.agent, "ai", None),
            original_reply=original_reply,
            incoming_message=message_payload,
            parsed_command=pending,
            command_status=_command_status_value(pending_goal),
            recent_private_context=recent_context,
            confirmed_actions=confirmed_actions,
            relationship=relationship,
            reply_length_mode=mode,
        )
        if attempt.text:
            rewritten = self.player_style.postprocess(attempt.text, mode=mode)
            rewritten_verdict = self.action_claim_guard.validate(
                rewritten,
                pending_command=pending,
                confirmed_actions=confirmed_actions,
            )
            if not rewritten_verdict.changed:
                return PrivateReplySafety(
                    original_reply=original_reply,
                    final_reply=rewritten,
                    guard_triggered=True,
                    rewritten=True,
                    rewrite_source="llm_safe_rewrite",
                    guard_reason=guarded.reason,
                )
            rewrite_reason = rewritten_verdict.reason
        else:
            rewrite_reason = attempt.error

        fallback = self.action_claim_rewriter.deterministic_fallback(
            parsed_command=pending,
            source_message_key=_optional_text(message_payload.get("source_message_key")),
        )
        fallback_verdict = self.action_claim_guard.validate(
            fallback,
            pending_command=pending,
            confirmed_actions=confirmed_actions,
        )
        if fallback_verdict.changed or not fallback.strip():
            # This is deliberately an emergency-only final line. It remains
            # independently safe under the same deterministic validator.
            fallback = "I'll check."
        if self.config.social_debug:
            self.log(f"[SAFETY] Using deterministic fallback: {rewrite_reason or 'rewrite unavailable'}")
        return PrivateReplySafety(
            original_reply=original_reply,
            final_reply=fallback,
            guard_triggered=True,
            rewritten=True,
            rewrite_source="deterministic_fallback",
            guard_reason=guarded.reason,
        )

    def _relationship_for(self, sender_uid: str) -> str:
        store = getattr(self.agent, "store", None)
        getter = getattr(store, "get_player_profile", None)
        if not callable(getter):
            return "UNKNOWN"
        try:
            profile = getter(sender_uid)
            return str(getattr(getattr(profile, "relationship", None), "value", "UNKNOWN"))
        except Exception:
            return "UNKNOWN"

    def _note_reply_candidate(self, goal: Goal) -> None:
        group = str(goal.payload.get("group") or "custom")
        sender_uid = str(goal.payload.get("sender_uid") or "")
        self.social_policy.note_reply(group=group, sender_uid=sender_uid, at=self.now())
        if group == "custom":
            self.social_initiative_manager.note_reply(at=self.now())
        if group == "custom":
            self.world_state.last_private_reply_at = self.now()
        elif group == "alliance":
            self.world_state.last_alliance_reply_at = self.now()

    def _handle_wait(self, decision: Decision, goal: Goal | None) -> None:
        if goal is None:
            return
        wait_state = str(getattr(decision, "wait_state", "") or "WAITING")
        if wait_state == "WAITING_FOR_USER_DECISION":
            # New PM goals remain queued while an older ambiguous PM blocks
            # the single send lane. No reply is generated yet, avoiding stale
            # messages and leaving capture/context processing fully active.
            goal.payload["accept_message_merge_when_blocked"] = True
            self.goal_manager.block_goal(
                goal.goal_id,
                reason="BLOCKED_BY_USER_DECISION:send_lane",
            )
            self.log(f"[RECOVERY] goal={goal.goal_id} state=WAITING_FOR_USER_DECISION")
            return
        if wait_state == "WAITING_FOR_DEPENDENCY":
            waits = int(goal.payload.get("dependency_wait_count") or 0) + 1
            goal.payload["dependency_wait_count"] = waits
            if waits >= max(1, int(self.config.max_dependency_retries)):
                self.goal_manager.block_goal(
                    goal.goal_id,
                    reason="BLOCKED_BY_NAVIGATION:dependency_retry_exhausted",
                )
                self.log(
                    f"[RECOVERY] goal={goal.goal_id} state=FAILED_SAFE "
                    "reason=dependency_retry_exhausted"
                )
                return
            self.goal_manager.defer_goal(
                goal.goal_id,
                reason=f"{wait_state}:{decision.reason}",
                not_before=self.now() + self.config.recovery_retry_delay,
            )
            self.log(
                f"[RECOVERY] goal={goal.goal_id} state={wait_state} "
                f"attempt={waits}/{self.config.max_dependency_retries}"
            )

    def _advance_private_reply_navigation(self, goal: Goal) -> None:
        """Plan one graph step toward the persistent verified PM target."""
        reply_id = str(goal.payload.get("pending_reply_id") or "")
        getter = getattr(self.agent, "get_pending_reply", None)
        pending = getter(reply_id) if callable(getter) and reply_id else None
        if pending is None:
            goal.payload.pop("pending_reply_id", None)
            goal.payload.pop("pending_reply_text", None)
            goal.payload.pop("pending_reply_source_keys", None)
            self.goal_manager.defer_goal(
                goal.goal_id,
                reason="WAITING_FOR_DEPENDENCY:pending_reply_missing",
                not_before=self.now() + self.config.recovery_retry_delay,
            )
            self.log(f"[RECOVERY] goal={goal.goal_id} state=NOT_ATTEMPTED reason=pending_reply_missing")
            return

        plan = self.navigation_manager.plan_private_conversation(
            goal,
            self.world_state,
            observed_this_tick=self._screen_observed_this_tick,
        )
        task = plan.task
        self._log_ui_effect_outcome(task)
        self.log(
            "[NAV GOAL] "
            f"id={task.navigation_id} parent_goal={goal.goal_id} "
            f"target={task.target_type} uid={task.target_uid} room={task.target_room_id} "
            f"status={task.status}"
        )
        if plan.kind == "TARGET_REACHED":
            self.log(
                "[NAV TARGET REACHED] "
                f"id={task.navigation_id} parent_goal={goal.goal_id} "
                f"uid={task.target_uid} room={task.target_room_id}"
            )
            self._send_pending_reply(goal, Decision(
                DecisionType.SEND_PRIVATE_REPLY,
                "Verified navigation target reached.",
                goal_id=goal.goal_id,
            ))
            return
        if plan.kind == "BLOCKED":
            self.goal_manager.block_goal(
                goal.goal_id,
                reason=f"BLOCKED_BY_NAVIGATION:{plan.reason}",
            )
            self.world_state.current_goal_id = None
            self.log(
                f"[NAV RESULT] id={task.navigation_id} state=NAVIGATION_BLOCKED reason={plan.reason}"
            )
            return
        if plan.kind == "WAIT":
            wait_interval = max(
                0.05,
                min(float(self.config.ui_effect_poll_interval), 0.25),
            )
            if plan.reason in {
                "waiting_for_ui_effect",
                "navigation_block_grace",
                "private_chat_stability",
            }:
                if self._screen_observed_this_tick and task.ui_effect_active:
                    elapsed = _effect_elapsed_ms(task, self.now())
                    self.log(
                        "[UI EFFECT OBSERVE] "
                        f"nav={task.navigation_id} action={task.ui_effect_action_name or '-'} "
                        f"screen={self.world_state.screen} "
                        f"confidence={_format_observation_confidence(self.world_state.screen_confidence)} "
                        f"elapsed_ms={elapsed}"
                    )
                if plan.reason == "private_chat_stability":
                    stable = (
                        task.stable_screen == "PRIVATE_CHAT"
                        and task.stable_screen_samples >= self.navigation_manager.min_private_chat_samples
                    )
                    self.log(
                        "[STABILITY] "
                        f"screen=PRIVATE_CHAT samples={task.stable_screen_samples} "
                        f"stable={str(stable).lower()}"
                    )
                not_before = self.now() + wait_interval
            else:
                not_before = self.now() + max(
                    self.config.recovery_retry_delay,
                    min(self.config.screen_poll_interval, 0.5),
                )
            self.goal_manager.defer_goal(
                goal.goal_id,
                reason=f"WAITING_FOR_NAVIGATION:{plan.reason}",
                not_before=not_before,
            )
            self.log(
                f"[NAV PLAN] id={task.navigation_id} from={self.world_state.screen} "
                f"target={task.target_type} next_action=reobserve reason={plan.reason}"
            )
            return

        assert plan.action is not None
        action = build_private_reply_action(pending)
        action.update({
            "action": plan.action,
            "private_reply_navigation": True,
            "navigation_id": task.navigation_id,
            "navigation_target": task.target_type,
            "expected_possible_results": list(plan.expected_possible_results),
        })
        action["goal_id"] = goal.goal_id
        action["action_id"] = uuid.uuid4().hex
        self.navigation_manager.action_started(
            goal,
            task,
            plan.action,
            self.world_state.screen,
            action_id=str(action["action_id"]),
            allowed_result_screens=plan.expected_possible_results,
        )
        self.log(
            "[NAV PLAN] "
            f"id={task.navigation_id} from={self.world_state.screen} target={task.target_type} "
            f"next_action={plan.action} route={','.join(plan.expected_possible_results)}"
        )
        if plan.action == "recover_back":
            recovery_type = (
                "safe_anchor"
                if self.world_state.screen == "UNKNOWN"
                else "normalize_private_conversation"
            )
            self.log(
                f"[RECOVERY] id={task.navigation_id} type={recovery_type} "
                f"from={self.world_state.screen}"
            )
        if plan.action == "prepare_private_reply_target":
            self.log(
                "[STABILITY] "
                f"screen=PRIVATE_CHAT samples={task.stable_screen_samples} stable=true"
            )
        self.log(
            f"[NAV ACTION] id={task.navigation_id} action={plan.action} "
            f"from={self.world_state.screen}"
        )
        self._execute_action(goal, action)

    def _send_pending_reply(self, goal: Goal, decision: Decision) -> None:
        task = self.navigation_manager.ensure_private_conversation_task(goal)
        target_is_verified = (
            self.world_state.screen == "PRIVATE_CONVERSATION"
            and self.world_state.current_conversation_verified
            and self.world_state.current_conversation_uid == task.target_uid
            and self.world_state.current_conversation_room_id == task.target_room_id
        )
        if not target_is_verified:
            self.log(
                f"[REPLAN] id={task.navigation_id} reason=send_entry_target_not_verified "
                f"from={self.world_state.screen}"
            )
            self._advance_private_reply_navigation(goal)
            return
        reply_id = str(goal.payload.get("pending_reply_id") or "")
        getter = getattr(self.agent, "get_pending_reply", None)
        pending = getter(reply_id) if callable(getter) and reply_id else None
        if pending is None:
            # Missing in-memory state is safely recoverable: discard only the
            # stale pending marker and regenerate from the still queued PMs.
            goal.payload.pop("pending_reply_id", None)
            goal.payload.pop("pending_reply_text", None)
            goal.payload.pop("pending_reply_source_keys", None)
            self.goal_manager.defer_goal(
                goal.goal_id,
                reason="WAITING_FOR_DEPENDENCY:pending_reply_missing",
                not_before=self.now() + self.config.recovery_retry_delay,
            )
            self.log(f"[RECOVERY] goal={goal.goal_id} state=NOT_ATTEMPTED reason=pending_reply_missing")
            return

        action = build_private_reply_action(pending)
        action["goal_id"] = goal.goal_id
        action["action_id"] = uuid.uuid4().hex
        self._execute_action(goal, action, pending_reply_id=reply_id)

    def _execute_action(
        self,
        goal: Goal,
        action: Mapping[str, Any],
        *,
        pending_reply_id: str | None = None,
    ) -> None:
        action_name = str(action.get("action") or "unknown")
        is_private_navigation = bool(action.get("private_reply_navigation"))
        self.world_state.game_busy = True
        self.log(f"[ACTION] {action_name} action={action.get('action_id') or '-'} goal={goal.goal_id}")
        try:
            result = self.executor.execute(dict(action), self.world_state.snapshot())
        except Exception as exc:
            self.world_state.game_busy = False
            if action_name == "reply_private":
                self._recover_safe_delivery_failure(
                    goal,
                    action,
                    delivery_metadata({
                        "delivery_state": DeliveryState.FAILED_SAFE.value,
                        "reason": f"executor_exception:{type(exc).__name__}",
                    }),
                )
            elif is_private_navigation:
                self._record_private_navigation_result(
                    goal,
                    action,
                    ok=False,
                    status="executor_exception",
                    reason=f"executor_exception:{type(exc).__name__}",
                )
            else:
                self._fail_goal(goal, f"action_error:{type(exc).__name__}: {exc}", action=action_name)
            return

        ok = bool(getattr(result, "ok", False))
        status = str(getattr(result, "status", "unknown"))
        result_data = getattr(result, "data", {})
        result_data = result_data if isinstance(result_data, Mapping) else {}
        delivery = delivery_metadata(result_data) if action_name in {
            "reply_private", "prepare_private_reply_target",
        } else None
        if delivery is not None and delivery["navigation_verified"]:
            self.world_state.set_current_conversation_verified(
                uid=str(result_data.get("current_conversation_uid") or action.get("target_uid") or ""),
                room_id=str(result_data.get("current_conversation_room_id") or action.get("room_id") or ""),
                verified_at=self.now(),
            )
        result_event = PlayerEvent(
            event_type=EventType.ACTION_COMPLETED if ok else EventType.ACTION_FAILED,
            source="action_executor",
            payload={
                "action": action_name,
                "action_id": action.get("action_id"),
                "status": status,
                "goal_id": goal.goal_id,
                "delivery": delivery,
            },
        )
        self.publish(result_event)
        if self.config.record_events:
            self.recorder.record("actions", result_event.to_dict())

        if not ok:
            if action_name == "reply_private":
                if is_ambiguous_delivery(delivery):
                    self._create_ambiguous_delivery_request(goal, action, delivery)
                else:
                    self._recover_safe_delivery_failure(goal, action, delivery)
            elif is_private_navigation:
                reason = str((delivery or {}).get("reason") or status)
                self._record_private_navigation_result(
                    goal,
                    action,
                    ok=False,
                    status=status,
                    reason=reason,
                )
            else:
                self._fail_goal(goal, f"action_failed:{status}", action=action_name)
            return

        if is_private_navigation:
            self._record_private_navigation_result(
                goal,
                action,
                ok=True,
                status=status,
                reason=None,
            )
            return

        if action_name == "reply_private" and pending_reply_id:
            if delivery is None or delivery["delivery_state"] != DeliveryState.CONFIRMED.value:
                self._recover_safe_delivery_failure(
                    goal,
                    action,
                    delivery or delivery_metadata({"reason": "missing_delivery_metadata"}),
                )
                return
            self._mark_pending_reply_sent(pending_reply_id)
            goal.payload["delivery_confirmation_source"] = "server_echo"
            self._record_confirmed_social_reply(goal)
            if goal.payload.get("proactive") and self.humanization_store is not None:
                proactive_id = str(goal.payload.get("proactive_id") or "")
                if proactive_id:
                    try:
                        self.humanization_store.confirm_proactive(proactive_id)
                        self.log(
                            f"[PROACTIVE SEND] uid={goal.payload.get('sender_uid')} "
                            f"id={proactive_id} confirmation=server_echo"
                        )
                    except Exception as exc:
                        self.log(f"[PROACTIVE SEND] confirmation persistence failed: {type(exc).__name__}")
            self._consume_reply_source_messages(goal, completed_reason="reply_sent")
            return

        self.goal_manager.complete_goal(goal.goal_id, reason=f"action_completed:{action_name}")
        self.world_state.current_goal_id = None

    def _record_private_navigation_result(
        self,
        goal: Goal,
        action: Mapping[str, Any],
        *,
        ok: bool,
        status: str,
        reason: str | None,
    ) -> None:
        """Persist a navigation result, then require a fresh observation."""
        task = self.navigation_manager.action_result(
            goal,
            action,
            ok=ok,
            result=status,
            reason=reason,
        )
        goal.attempts = 0
        # The action result only proves the ADB/verification call returned.
        # The screen effect must settle through observation before any next tap.
        self._drain_events()
        if task.ui_effect_active:
            timeout_ms = max(
                0,
                int(((task.ui_effect_deadline_at or self.now()) - self.now()) * 1000),
            )
            self._next_screen_poll_at = min(
                self._next_screen_poll_at,
                self.now() + max(0.05, float(self.config.ui_effect_poll_interval)),
            )
            self.log(
                "[UI EFFECT WAIT] "
                f"nav={task.navigation_id} action={task.ui_effect_action_name or action.get('action') or '-'} "
                f"source={task.ui_effect_source_screen or self.world_state.screen} "
                f"timeout_ms={timeout_ms}"
            )
        self.goal_manager.defer_goal(
            goal.goal_id,
            reason=(
                "WAITING_FOR_UI_EFFECT:navigation_action"
                if task.ui_effect_active else (
                    "WAITING_FOR_OBSERVATION:navigation_action"
                    if ok else f"WAITING_FOR_OBSERVATION:navigation_failure:{reason or status}"
                )
            ),
            not_before=self.now() + (
                max(0.05, float(self.config.ui_effect_poll_interval))
                if task.ui_effect_active else max(
                    self.config.recovery_retry_delay,
                    self.config.screen_poll_interval,
                )
            ),
        )
        self.log(
            "[NAV RESULT] "
            f"id={task.navigation_id} action={action.get('action') or '-'} "
            f"ok={str(ok).lower()} status={status} actual_screen={self.world_state.screen} "
            f"reason={reason or '-'}"
        )
        if not ok and not task.ui_effect_active:
            self.log(
                f"[REPLAN] id={task.navigation_id} reason={reason or status} "
                f"from={self.world_state.screen}"
            )

    def _log_ui_effect_outcome(self, task: Any) -> None:
        outcome = str(getattr(task, "ui_effect_outcome", "") or "")
        action_id = str(getattr(task, "ui_effect_action_id", "") or "")
        if not outcome or not action_id:
            return
        marker = (str(task.navigation_id), action_id, outcome)
        if marker in self._reported_ui_effect_outcomes:
            return
        self._reported_ui_effect_outcomes.add(marker)
        if outcome == "confirmed":
            self.log(
                "[UI EFFECT CONFIRMED] "
                f"nav={task.navigation_id} action={task.ui_effect_action_name or '-'} "
                f"result_screen={task.ui_effect_result_screen or '-'} "
                f"classification={task.ui_effect_result_classification or '-'} "
                f"elapsed_ms={task.ui_effect_elapsed_ms or 0}"
            )
        elif outcome == "timeout":
            self.log(
                "[UI EFFECT TIMEOUT] "
                f"nav={task.navigation_id} action={task.ui_effect_action_name or '-'} "
                f"last_screen={task.ui_effect_result_screen or '-'} "
                f"classification={task.ui_effect_result_classification or '-'} "
                f"elapsed_ms={task.ui_effect_elapsed_ms or 0}"
            )

    def _navigation_observation_pending(self) -> bool:
        for item in self.goal_manager.snapshot():
            payload = item.get("payload") if isinstance(item, Mapping) else None
            task = payload.get("navigation_task") if isinstance(payload, Mapping) else None
            if isinstance(task, Mapping) and (
                bool(task.get("ui_effect_active"))
                or bool(task.get("pending_block_reason"))
            ):
                return True
        return False

    def _resume_safe_navigation_blocks_on_screen_change(self) -> None:
        """Re-plan a no-send navigation block after a meaningful external UI change."""
        if self.world_state.screen in {"UNKNOWN", "CHAT_OPEN_UNCLASSIFIED"}:
            return
        for goal in self.goal_manager.get_blocked_goals():
            if goal.goal_type != GoalType.ANSWER_PRIVATE_MESSAGE:
                continue
            if not goal.payload.get("pending_reply_id"):
                continue
            reason = str(goal.blocked_reason or "")
            if not reason.startswith("BLOCKED_BY_NAVIGATION:"):
                continue
            if "navigation_target_incomplete" in reason:
                continue
            task = self.navigation_manager.resume_after_external_observation(goal)
            self.goal_manager.resume_goal(
                goal.goal_id,
                reason="external_ui_transition_after_navigation_block",
            )
            self.log(
                f"[NAV RESUME] id={task.navigation_id} goal={goal.goal_id} "
                f"screen={self.world_state.screen}"
            )

    def _mark_pending_reply_sent(self, reply_id: str) -> None:
        marker = getattr(self.agent, "mark_reply_sent", None)
        if not callable(marker):
            return
        try:
            marker(reply_id)
        except TypeError:
            marker(reply_id=reply_id)

    def _recover_safe_delivery_failure(
        self,
        goal: Goal,
        action: Mapping[str, Any],
        delivery: Mapping[str, Any],
    ) -> None:
        """Retry only a delivery proved not to have reached the send tap."""
        values = delivery_metadata(delivery)
        if values["send_tap_attempted"]:
            # Defensive belt-and-braces: any uncertain post-tap condition must
            # be treated as ambiguous rather than automatically re-sent.
            self._create_ambiguous_delivery_request(goal, action, values)
            return
        reason = str(values["reason"])
        if reason.startswith("private_chat_not_ready:"):
            # Defensive recovery for old/stale callers: turn a pre-tap
            # wrong-screen failure into route recovery, never a blind resend.
            reported_screen = reason.split(":", 1)[1].strip().upper()
            if reported_screen:
                self.world_state.update_screen(reported_screen, updated_at=self.now())
            task = self.navigation_manager.reset_after_wrong_screen_delivery(goal, reason)
            goal.attempts = 0
            self.goal_manager.defer_goal(
                goal.goal_id,
                reason=f"WAITING_FOR_OBSERVATION:wrong_screen_delivery:{reason}",
                not_before=self.now() + max(
                    self.config.recovery_retry_delay,
                    self.config.screen_poll_interval,
                ),
            )
            self.log(
                f"[REPLAN] id={task.navigation_id} reason={reason} "
                f"from={self.world_state.screen}"
            )
            return
        updated = self.goal_manager.fail_goal(
            goal.goal_id,
            reason=f"FAILED_SAFE:{reason}",
            retry=True,
        )
        if updated.status == GoalStatus.PENDING:
            updated.not_before = self.now() + self.config.recovery_retry_delay
        self.log(
            f"[RECOVERY] action={action.get('action_id') or '-'} goal={goal.goal_id} "
            f"state=FAILED_SAFE reason={reason} "
            f"next={updated.status.value}"
        )

    def _create_ambiguous_delivery_request(
        self,
        goal: Goal,
        action: Mapping[str, Any],
        delivery: Mapping[str, Any],
    ) -> None:
        values = delivery_metadata(delivery)
        reply_id = str(goal.payload.get("pending_reply_id") or action.get("reply_id") or "") or None
        request, created = self.user_decision_manager.create_or_get(
            request_type="AMBIGUOUS_DELIVERY",
            question="Private reply may have been sent. Choose retry, cancel, or assume-sent.",
            reason=str(values["reason"]),
            options=["retry", "cancel", "assume-sent"],
            related_goal_id=goal.goal_id,
            related_action_id=str(action.get("action_id") or "") or None,
            related_reply_id=reply_id,
        )
        goal.payload["user_decision_request_id"] = request.request_id
        goal.payload["accept_message_merge_when_blocked"] = True
        self.goal_manager.block_goal(
            goal.goal_id,
            reason="BLOCKED_BY_USER_DECISION:AMBIGUOUS_DELIVERY",
        )
        self.log(
            f"[DELIVERY] action={action.get('action_id') or '-'} state=AMBIGUOUS "
            f"send_tap={str(values['send_tap_attempted']).lower()} "
            f"echo={str(values['server_echo_seen']).lower()} reason={values['reason']}"
        )
        if not created:
            return
        self.last_user_decision = request
        self.publish(
            PlayerEvent(
                event_type=EventType.USER_DECISION_REQUIRED,
                source="player_loop",
                payload=request.to_dict(),
            )
        )
        self.log(
            f"[USER DECISION] request={request.request_id} state=PENDING "
            f"goal={goal.goal_id} reply_id={reply_id or '-'} reason={request.reason}"
        )

    def _process_terminal_decision_commands(self) -> None:
        for command, request_id in self.user_decision_manager.drain_terminal_commands():
            if command == "list":
                for request in self.user_decision_manager.pending():
                    self.log(
                        f"[USER DECISION] request={request.request_id} state=PENDING "
                        f"options={','.join(request.options)}"
                    )
                continue
            if request_id is None:
                continue
            try:
                self.resolve_user_decision(request_id, command)
            except (KeyError, ValueError) as exc:
                self.log(f"[USER DECISION] command={command} request={request_id} rejected={exc}")

    def resolve_user_decision(self, request_id: str, resolution: str) -> UserDecisionRequest:
        request = self.user_decision_manager.resolve(request_id, resolution)
        self.publish(
            PlayerEvent(
                event_type=EventType.USER_DECISION_RECEIVED,
                source="terminal_decision_control",
                payload=request.to_dict(),
            )
        )
        goal = self.goal_manager.get_goal(request.related_goal_id)
        if goal is not None:
            if request.resolution == "retry":
                self.goal_manager.resume_goal(goal.goal_id, reason="user_retry_requested")
            elif request.resolution == "cancel":
                self._discard_pending_reply(goal)
                self._consume_reply_source_messages(goal, completed_reason="reply_cancelled_by_user")
            elif request.resolution == "assume-sent":
                if request.related_reply_id:
                    self._mark_pending_reply_sent(request.related_reply_id)
                goal.payload["delivery_confirmation_source"] = "manual_assumption"
                self._record_confirmed_social_reply(goal)
                self._consume_reply_source_messages(goal, completed_reason="reply_manual_assumption")
                manual_event = PlayerEvent(
                    event_type=EventType.ACTION_COMPLETED,
                    source="user_decision_manager",
                    payload={
                        "action": "reply_private",
                        "action_id": request.related_action_id,
                        "goal_id": goal.goal_id,
                        "status": "manual_assumption",
                        "confirmation_source": "manual_assumption",
                    },
                )
                self.publish(manual_event)
                if self.config.record_events:
                    self.recorder.record("actions", manual_event.to_dict())
                self.log(
                    f"[DELIVERY] action={request.related_action_id or '-'} "
                    "state=MANUAL_ASSUMPTION source=manual_assumption"
                )
        self._resume_send_lane_goals()
        self.log(
            f"[USER DECISION] request={request.request_id} state={request.status.value} "
            f"resolution={request.resolution}"
        )
        return request

    def _resume_send_lane_goals(self) -> None:
        if self.user_decision_manager.pending_delivery() is not None:
            return
        for snapshot in self.goal_manager.snapshot():
            if str(snapshot.get("blocked_reason") or "") == "BLOCKED_BY_USER_DECISION:send_lane":
                self.goal_manager.resume_goal(str(snapshot["goal_id"]), reason="send_lane_resumed")

    def _discard_pending_reply(self, goal: Goal) -> None:
        reply_id = str(goal.payload.get("pending_reply_id") or "")
        discard = getattr(self.agent, "discard_reply", None)
        if reply_id and callable(discard):
            discard(reply_id)
        goal.payload.pop("pending_reply_id", None)
        goal.payload.pop("pending_reply_text", None)

    def _consume_reply_source_messages(self, goal: Goal, *, completed_reason: str) -> None:
        source_keys = set(_message_source_keys(goal.payload.get("pending_reply_source_keys")))
        messages = goal.payload.get("messages")
        if isinstance(messages, list):
            if source_keys:
                messages[:] = [
                    message for message in messages
                    if not isinstance(message, Mapping)
                    or str(message.get("source_message_key") or "") not in source_keys
                ]
            else:
                messages.clear()
        goal.payload.pop("pending_reply_id", None)
        goal.payload.pop("pending_reply_text", None)
        goal.payload.pop("pending_reply_source_keys", None)
        goal.payload.pop("user_decision_request_id", None)
        goal.payload.pop("accept_message_merge_when_blocked", None)
        if isinstance(messages, list) and messages:
            goal.attempts = 0
            self.goal_manager.resume_goal(goal.goal_id, reason="queued_messages_after_delivery")
            return
        self.goal_manager.complete_goal(goal.goal_id, reason=completed_reason)
        self.world_state.current_goal_id = None
        self.log(f"[GOAL] completed {goal.goal_id} reason={completed_reason}")

    def _fail_goal(self, goal: Goal, reason: str, *, action: str = "generate_reply") -> None:
        updated = self.goal_manager.fail_goal(goal.goal_id, reason=reason, retry=True)
        event = PlayerEvent(
            event_type=EventType.ACTION_FAILED,
            source="player_loop",
            payload={"action": action, "status": updated.status.value, "goal_id": goal.goal_id},
        )
        self.publish(event)
        self.log(
            f"[GOAL] {updated.status.value} {goal.goal_id} "
            f"attempt={updated.attempts}/{updated.max_attempts} reason={reason}"
        )

    def _record_confirmed_social_reply(self, goal: Goal) -> None:
        recorder = getattr(getattr(self.agent, "store", None), "record_social_reply", None)
        if callable(recorder):
            try:
                recorder(uid=str(goal.payload.get("sender_uid") or ""), sent=True)
            except Exception as exc:
                self.log(f"[SOCIAL] confirmed-reply profile update failed: {type(exc).__name__}: {exc}")
        if self.humanization_store is not None:
            try:
                self.humanization_store.record_outgoing(str(goal.payload.get("sender_uid") or ""), at=self.now())
            except Exception as exc:
                self.log(f"[HUMAN PROFILE] outgoing update failed: {type(exc).__name__}")

    def _consume_goal_message(
        self,
        goal: Goal,
        *,
        completed_reason: str,
        consume_all: bool = False,
    ) -> None:
        messages = goal.payload.get("messages")
        if consume_all and isinstance(messages, list):
            messages.clear()
        elif isinstance(messages, list) and messages:
            messages.pop(0)
        goal.payload.pop("pending_reply_id", None)
        goal.payload.pop("pending_reply_text", None)
        if isinstance(messages, list) and messages:
            # This is the next coalesced incoming message, not a retry.
            goal.attempts = 0
            self.goal_manager.requeue_goal(goal.goal_id)
            return
        self.goal_manager.complete_goal(goal.goal_id, reason=completed_reason)
        self.world_state.current_goal_id = None
        self.log(f"[GOAL] completed {goal.goal_id} reason={completed_reason}")

    def _request_user_decision(
        self,
        decision: Decision,
        goal: Goal | None,
        *,
        question: str | None = None,
    ) -> None:
        # Retained as a compatibility hook for callers from older stages.
        # It never creates a request by itself, preventing tick-driven spam.
        del question
        self.log(
            f"[USER DECISION] suppressed legacy_request decision={decision.decision_id} "
            f"goal={goal.goal_id if goal else '-'}"
        )


def _batched_goal_message(goal: Goal) -> dict[str, Any] | None:
    """Use one latest envelope with the short batch as its input text."""
    messages = goal.payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    valid = [dict(item) for item in messages if isinstance(item, Mapping)]
    if not valid:
        return None
    message = valid[-1]
    texts = [str(item.get("text") or "").strip() for item in valid]
    message["text"] = "\n".join(text for text in texts if text)
    return message


def _message_source_keys(values: Any) -> list[str]:
    """Return non-empty source keys from message envelopes or a saved key list."""
    if not isinstance(values, (list, tuple)):
        return []
    keys: list[str] = []
    for value in values:
        key = (
            str(value.get("source_message_key") or "")
            if isinstance(value, Mapping)
            else str(value or "")
        )
        if key and key not in keys:
            keys.append(key)
    return keys


def _chat_message_from_payload(payload: Mapping[str, Any]) -> Any:
    """Recreate the existing ChatMessage object for the existing agent API."""
    from archive.legacy_runtime.chat_capture import ChatMessage

    return ChatMessage(
        room_id=str(payload.get("room_id") or ""),
        group=str(payload.get("group") or "custom"),
        sender_uid=str(payload.get("sender_uid") or ""),
        sender_name=_optional_text(payload.get("sender_name")),
        text=str(payload.get("text") or ""),
        seq_id=_optional_int(payload.get("seq_id")),
        server_time=_optional_int(payload.get("server_time")),
        send_time=_optional_int(payload.get("send_time")),
        sender_alliance=_optional_text(payload.get("sender_alliance")),
        sender_server_id=_optional_int(payload.get("sender_server_id")),
        message_type=_optional_int(payload.get("message_type")),
        post_type=_optional_int(payload.get("post_type")),
        original_lang=_optional_text(payload.get("original_lang")),
        pushseq=_optional_text(payload.get("pushseq")),
        raw_data=dict(payload.get("raw_data") or {}),
    )


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format_observation_confidence(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _human_memory_type(value: str) -> str:
    mapping = {
        "identity": "IDENTITY",
        "preference": "PREFERENCE",
        "playstyle": "GAME_PREFERENCE",
        "schedule": "SCHEDULE",
        "social": "SOCIAL",
        "relationship": "RELATIONSHIP",
        "recurring_topic": "RECURRING_TOPIC",
    }
    return mapping.get(str(value or "").strip().lower(), "OTHER")


def _style_candidate_from_text(text: str) -> dict[str, Any] | None:
    """Only explicit, human-readable style requests become review candidates."""
    value = " ".join(str(text or "").casefold().split())
    if not value:
        return None
    if "sound softer" in value or "be nicer" in value:
        return {"softness": 0.75, "directness": 0.30, "tags": ["soft", "friendly"]}
    if "more catty" in value or "more playful" in value:
        return {"playfulness": 0.78, "teasing": 0.45, "tags": ["playful"]}
    if "too direct" in value:
        return {"directness": 0.25, "softness": 0.70}
    if "less emoji" in value:
        return {"emoji_level": "LOW"}
    if "more emoji" in value:
        return {"emoji_level": "HIGH"}
    return None


def _effect_elapsed_ms(task: Any, now: float) -> int:
    try:
        started = float(getattr(task, "ui_effect_started_at", now) or now)
        return max(0, int((float(now) - started) * 1000))
    except (TypeError, ValueError):
        return 0


def _payload_timestamp_ms(payload: Mapping[str, Any], fallback_seconds: float) -> int:
    server_time = _optional_int(payload.get("server_time"))
    if server_time is not None:
        return server_time
    send_time = _optional_int(payload.get("send_time"))
    if send_time is not None:
        return send_time * 1000
    return int(float(fallback_seconds) * 1000)


def _analysis_intent_value(analysis: MessageAnalysis | Mapping[str, Any]) -> str:
    if isinstance(analysis, Mapping):
        return str(analysis.get("intent") or "UNKNOWN")
    value = getattr(getattr(analysis, "intent", None), "value", None)
    return str(value or "UNKNOWN")


def _command_type_value(command: Any | None) -> str | None:
    if command is None:
        return None
    if isinstance(command, Mapping):
        value = command.get("command_type")
    else:
        value = getattr(command, "command_type", None)
        value = getattr(value, "value", value)
    text = str(value or "").strip()
    return text or None


def _command_status_value(command: Any | None) -> str | None:
    if command is None:
        return None
    if isinstance(command, Mapping):
        value = command.get("status")
    else:
        value = getattr(command, "status", None)
        value = getattr(value, "value", value)
    text = str(value or "").strip()
    return text or None


def _highest_risk_pending_command(commands: Any) -> Any | None:
    """Return the most safety-sensitive unconfirmed command in a batch."""
    if not isinstance(commands, (list, tuple)):
        return None
    selected: Any | None = None
    highest = -1
    for command in commands:
        if command is None:
            continue
        if isinstance(command, Mapping):
            risk = str(command.get("risk_level") or "LOW").upper()
        else:
            risk = str(getattr(getattr(command, "risk_level", None), "value", "LOW")).upper()
        score = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(risk, 1)
        if score >= highest:
            selected = command
            highest = score
    return selected


def _pending_goal_for_command(pending_commands: Any, parsed_command: Any | None) -> Any | None:
    """Find status metadata for the parsed command selected by the guard."""
    if not isinstance(pending_commands, (list, tuple)):
        return None
    command_type = _command_type_value(parsed_command)
    for pending in reversed(pending_commands):
        if _command_type_value(pending) == command_type:
            return pending
    return None

