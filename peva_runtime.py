"""Centralny runtime PEva z wąską, świadomie zatwierdzaną odpowiedzią PM.

Zwykły start pozostaje w SAFE.  Jedyna ścieżka fizyczna jest ograniczona do
otwartej, świeżo odczytanej prywatnej rozmowy z dokładnym aliasem znajomego,
którego użytkownik osobno zatwierdził w panelu.  Nie importuje starego
executor'a ADB i nie potrafi wybrać osoby z listy ani wysłać do świata/grupy.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol

from contracts import Action, ActionKind, BrainInput, Decision, DecisionKind, MessageObservation, VisionResult
from emocje import Emotions
from mozg import BrainContextBuilder, BrainDecisionEngine, ConversationFlow, GameKnowledgeBase, LocalModel, ModelStatus
from mozg.friendships import FriendCandidate, FriendshipStore
from oczy.identity import parse_private_header
from oczy.packets import ChatPacket, PacketBurstBuffer, PacketReadEvent, PrivateChatPacketReader
from oczy.session_history import SessionHistory, is_last_z_animated_emote_text
from rece import ADBApprovedPrivateReplySender, ApprovedPrivateReplySender, DeliveryReceipt, DeliveryState, SafetyGate, SafetyState
from config.runtime_settings import PevaSettings, load_settings


class PacketReader(Protocol):
    def poll_once(self) -> PacketReadEvent: ...


class RuntimeModel(Protocol):
    @property
    def status(self) -> ModelStatus: ...

    def start(self) -> ModelStatus: ...

    def close(self) -> None: ...

    def generate_reply_safe(self, incoming_text: str, **kwargs: object): ...


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Status techniczny dla panelu; nigdy nie zawiera treści wiadomości."""

    at_ms: int
    category: str
    detail: str


class PEvaRuntime:
    """Jedna PEva: model, Oczy, Mózg, Charakter, Emocje i globalny SAFE.

    Pętla Oczu jest osobnym lekkim wątkiem.  Skan ma miejsce najwyżej raz na
    sekundę i dopiero potwierdzony sygnał inicjuje jednorazowy OCR.  Model jest
    ładowany raz przy ``start`` i używany dopiero po zamknięciu krótkiej paczki
    wiadomości.  W tej wersji każda decyzja kończy się ``Action(NONE)``.
    """

    def __init__(
        self,
        *,
        settings: PevaSettings | None = None,
        model: RuntimeModel | None = None,
        reader_factory: Callable[[], PacketReader] = PrivateChatPacketReader,
        interval_seconds: float = 1.0,
        friendships: FriendshipStore | None = None,
        game_knowledge: GameKnowledgeBase | None = None,
        sender: ApprovedPrivateReplySender | None = None,
    ) -> None:
        if interval_seconds < 0.5:
            raise ValueError("interval_seconds must be at least 0.5")
        self.settings = settings or load_settings()
        self.emotions = Emotions()
        self.context_builder = BrainContextBuilder(emotions=self.emotions)
        self.model: RuntimeModel = model or LocalModel(settings=self.settings)
        self.decision_engine = BrainDecisionEngine(self.model)
        self.safety = SafetyGate()
        self._reader_factory = reader_factory
        self._interval_seconds = float(interval_seconds)
        self._reader: PacketReader | None = None
        self._burst = PacketBurstBuffer()
        self._running = False
        self._eyes_enabled = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._events: deque[RuntimeEvent] = deque(maxlen=80)
        self.friendships = friendships or FriendshipStore()
        # This is a small local file of dated community notes.  It never
        # fetches a wiki at runtime and it is not a substitute for the game UI.
        self.game_knowledge = game_knowledge or GameKnowledgeBase.load()
        # This is deliberately a narrowly shaped sender: it cannot navigate
        # the PM list or accept a target UID.  It verifies the visible header
        # again immediately before input.
        self._sender = sender or ADBApprovedPrivateReplySender()
        self._session_history = SessionHistory()
        self._conversation_flow = ConversationFlow()
        self._last_context: BrainInput | None = None
        self._last_decision: Decision | None = None
        self._last_action = Action(ActionKind.NONE)
        self._last_packet_shape: dict[str, object] | None = None
        self._last_history_turn_count = 0
        self._last_delivery: DeliveryReceipt | None = None
        self._last_delivery_at_by_alias: dict[str, float] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def eyes_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_decision(self) -> Decision | None:
        return self._last_decision

    def start(self, *, load_model: bool = True, start_eyes: bool = True) -> None:
        """Uruchom całą PEvę w SAFE; ewentualny błąd modelu nie uruchamia akcji."""
        with self._lock:
            self._running = True
            self.safety.stop("PEva started in SAFE mode")
            self._record("runtime", "PEva started in SAFE mode.")
        if load_model:
            self.start_model()
        else:
            self._record("model", "Model loading was skipped for this test session.")
        if start_eyes:
            self.start_eyes()

    def start_eyes(self) -> bool:
        """Włącz lekkie czuwanie.  Nie wykonuje ono modelu ani akcji Rąk."""
        with self._lock:
            if not self._running:
                raise RuntimeError("Start PEva before starting Eyes.")
            if self.eyes_running:
                return False
            if self._reader is None:
                self._reader = self._reader_factory()
            self._stop_event.clear()
            self._eyes_enabled = True
            self._thread = threading.Thread(target=self._eye_loop, name="PEvaEyes", daemon=True)
            self._thread.start()
            self._record("eyes", "Eyes standby started at one scan per second.")
            return True

    def start_model(self) -> ModelStatus:
        """Załaduj model wyłącznie podczas aktywnej sesji PEva.

        Jest to testowy przełącznik dla panelu. Zwykły start nadal ładuje
        model i Oczy razem; ta metoda nie uruchamia Rąk ani nie wznawia SAFE.
        """
        if not self._running:
            raise RuntimeError("Start PEva before starting its model.")
        status = self.model.start()
        self._record("model", status.detail)
        return status

    def stop_model(self) -> None:
        """Zatrzymaj tylko model należący do PEva, pozostawiając Oczy w SAFE."""
        self.model.close()
        self._record("model", "Local model stopped for this test session.")

    def stop_eyes(self) -> None:
        """Zatrzymaj czuwanie oraz Androidowy pomocnik, jeśli został użyty."""
        self._eyes_enabled = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._stop_android_helper()
        self._record("eyes", "Eyes standby stopped.")

    def stop_actions(self, reason: str = "Stopped by user") -> SafetyState:
        state = self.safety.stop(reason)
        self._record("safe", state.reason)
        return state

    def resume_actions(self, reason: str = "Resumed by user") -> SafetyState:
        """Allow only the separately approved automatic-PM reply lane."""
        state = self.safety.resume(reason)
        approved = len(self.friendships.approved_auto_reply_aliases())
        if approved:
            self._record("safe", "Approved private replies enabled for locally approved aliases only.")
        else:
            self._record("safe", "SAFE released, but no friend has automatic-reply approval yet.")
        return state

    def poll_eyes_once(self, *, now: float | None = None) -> Decision | None:
        """Wykonaj jeden krok Oczu; publiczne wejście przydatne w testach."""
        if not self._running:
            raise RuntimeError("PEva is not running.")
        reader = self._reader
        if reader is None:
            reader = self._reader_factory()
            self._reader = reader
        event = reader.poll_once()
        if event.kind == "overlay_open":
            # A Last Z modal can cover the chat while old bubbles remain
            # visible.  A pending burst is no longer safe to attribute or
            # reply to, so it is deliberately forgotten.
            self._burst.discard()
            self._conversation_flow.reset("conversation_covered_by_overlay")
            self._record("eyes", event.detail or "Conversation is covered by a modal window.")
            return None
        if event.packet is not None:
            self._burst.add(event.packet, now=now)
            self._conversation_flow.begin_collecting()
            self._last_packet_shape = {
                "characters": len(event.packet.text),
                "pages": event.packet.page_count,
                "complete": event.packet.complete,
                "contains_nontext": event.packet.contains_nontext_signal,
                "reason": event.packet.stop_reason,
                "structured_turns_available": event.packet.layout is not None,
            }
            self._record("eyes", f"Packet received: {event.packet.page_count} screen(s), no text persisted.")
        if self._burst.pending and self._burst.ready(now=now):
            packet = self._burst.take()
            assert packet is not None
            return self.process_packet(packet)
        if event.kind != "no_packet" and not event.packet:
            self._record("eyes", event.detail or event.kind)
        return None

    def process_packet(self, packet: ChatPacket) -> Decision:
        """Przekaż gotową paczkę Oczu do Mózgu, wyłącznie w pamięci runtime."""
        if packet.layout is not None:
            aliases = self.friendships.confirmed_aliases()
            session = self._session_history.observe(
                packet.conversation_header,
                packet.layout,
                trusted_aliases=aliases,
            )
            if session is None:
                return self._remember_decision(
                    Decision(DecisionKind.WAIT, "structured_conversation_incomplete")
                )
            header = parse_private_header(
                packet.conversation_header,
                trusted_aliases=aliases,
                dedicated_header=True,
            )
            assert header is not None
            candidate = self.friendships.observe_conversation(
                header,
                user_turns=1 + sum(turn.role == "user" for turn in session.history),
                assistant_turns=sum(turn.role == "assistant" for turn in session.history),
            )
            if candidate is not None and candidate.status.value == "REVIEW":
                self._record("friendship", "A human-reviewed friend proposal is available locally.")
            vision = VisionResult(
                "PRIVATE_CONVERSATION",
                min(0.95 if packet.complete else 0.70, header.confidence),
                (
                    MessageObservation(
                        session.incoming_text,
                        header.display_name,
                        min(0.95 if packet.complete else 0.70, header.confidence),
                        "private_chat_local_ocr_layout",
                    ),
                ),
                "private_packet_structured",
            )
            history = tuple(
                {"role": turn.role, "content": turn.content}
                for turn in session.history
            )
            flow = self._conversation_flow.permit(
                conversation_header=packet.conversation_header,
                incoming_text=session.incoming_text,
                history=history,
            )
            if not flow.allowed:
                self._record("conversation", "Same visible message burst was already handled; waiting for a new turn.")
                return self._remember_decision(Decision(DecisionKind.WAIT, flow.reason))
            decision = self.process_vision(vision, history=history)
            if decision.reason in {"local_model_not_ready", "local_model_error"}:
                self._conversation_flow.defer(decision.reason)
            else:
                self._conversation_flow.complete_turn(
                    conversation_header=packet.conversation_header,
                    incoming_text=session.incoming_text,
                    history=history,
                )
            self._maybe_deliver_approved_reply(decision, header.display_name)
            return decision
        vision = vision_from_packet(packet)
        return self.process_vision(vision)

    def process_vision(
        self,
        vision: VisionResult,
        *,
        memory_context: tuple[str, ...] = (),
        history: tuple[dict[str, str], ...] = (),
    ) -> Decision:
        """Wspólne wejście dla przyszłego resolvera rozmówcy lub testów.

        ``memory_context`` może pochodzić wyłącznie z resolvera opartego na
        niezależnie potwierdzonym UID. Runtime celowo nie szuka pamięci po
        nazwie rozpoznanej przez OCR, aby zmiana nicku nie połączyła rozmów
        dwóch różnych graczy.
        """
        newest_text = next(
            (item.text for item in reversed(vision.messages) if item.text and not item.nontext),
            "",
        )
        knowledge_notes = self.game_knowledge.for_message(newest_text or "")
        context = self.context_builder.build(
            vision,
            memory_context=(*memory_context, *knowledge_notes),
        )
        decision = self.decision_engine.decide(context, history=history)
        with self._lock:
            self._last_context = context
            self._last_history_turn_count = len(history)
        return self._remember_decision(decision)

    def _remember_decision(self, decision: Decision) -> Decision:
        """Store a preview; packet processing may later pass a narrow send gate."""
        action = Action(ActionKind.NONE)
        with self._lock:
            self._last_decision = decision
            self._last_action = action
            self._record("decision", f"{decision.kind.value}: {decision.reason}")
        return decision

    def _maybe_deliver_approved_reply(self, decision: Decision, alias: str) -> None:
        """Send only a newly generated reply in the exact open approved PM.

        This method is intentionally reachable only from the structured packet
        path.  Generic ``process_vision`` callers, old OCR, World/Alliance
        chat, and unapproved aliases retain a preview-only decision.
        """
        if decision.kind not in {
            DecisionKind.REPLY,
            DecisionKind.ASK_BACK,
            DecisionKind.CHANGE_TOPIC,
        }:
            return
        text = (decision.text or "").strip()
        if not text or self.safety.state.safe:
            return
        approved = {item.casefold() for item in self.friendships.approved_auto_reply_aliases()}
        if alias.casefold() not in approved:
            return
        now = time.monotonic()
        previous = self._last_delivery_at_by_alias.get(alias.casefold())
        if previous is not None and now - previous < 8.0:
            self._record("delivery", "A reply was recently attempted for this approved conversation; waiting.")
            return
        self._last_delivery_at_by_alias[alias.casefold()] = now
        receipt = self._sender.send_approved_reply(alias, text)
        with self._lock:
            self._last_delivery = receipt
            if receipt.send_tap_attempted:
                # Text remains RAM-only inside Action and is intentionally not
                # exported by status snapshots or event details.
                self._last_action = Action(ActionKind.SEND_MESSAGE, target=alias, text=text)
        if receipt.state is DeliveryState.CONFIRMED:
            self._record("delivery", "Approved private reply was visually confirmed on the current screen.")
            return
        if receipt.send_tap_attempted or receipt.text_entered:
            self.stop_actions("Automatic replies paused: delivery needs local review")
            self._record("delivery", "Reply was not fully confirmed; PEva will not retry it automatically.")
            return
        self._record("delivery", "Reply was blocked before text entry because the current conversation was not proven.")

    def status_snapshot(self) -> dict[str, object]:
        """Prywatny, JSON-ready stan dla lokalnego panelu."""
        model_status = self.model.status
        safe = self.safety.state
        decision = self._last_decision
        return {
            "running": self._running,
            "safe": safe.safe,
            "safe_reason": safe.reason,
            "eyes_enabled": self._eyes_enabled,
            "eyes_running": self.eyes_running,
            "model": {
                "configured": model_status.configured,
                "ready": model_status.ready,
                "name": model_status.model_name,
                "path": model_status.model_path,
                "detail": model_status.detail,
                "managed_by_peva": model_status.managed_by_peva,
            },
            "last_packet": self._last_packet_shape,
            "history_turns_in_memory": self._last_history_turn_count,
            "conversation_flow": self._conversation_flow.snapshot(),
            "friend_proposal_counts": self.friendships.status_counts(),
            "automatic_delivery": {
                "available": self._sender is not None,
                "enabled": not safe.safe,
                "approved_contacts": len(self.friendships.approved_auto_reply_aliases()),
                "last_state": None if self._last_delivery is None else self._last_delivery.state.value,
                "last_reason": None if self._last_delivery is None else self._last_delivery.reason,
            },
            "local_game_knowledge_facts": len(self.game_knowledge.snapshot()),
            "last_decision": None if decision is None else {
                "kind": decision.kind.value,
                "reason": decision.reason,
                "has_preview_text": bool(decision.text),
            },
            "last_action": self._last_action.kind.value,
            "physical_actions_connected": not safe.safe,
            "recent_events": [
                {"at_ms": item.at_ms, "category": item.category, "detail": item.detail}
                for item in self._events
            ],
        }

    def preview_snapshot(self) -> dict[str, object]:
        """Zwróć ulotną propozycję odpowiedzi wyłącznie lokalnemu panelowi.

        Treść przychodzącej rozmowy nie trafia ani do statusu, ani do tego
        widoku. Podgląd znika wraz z bieżącym runtime'em i nie jest zapisywany
        do pliku ani przekazywany do Rąk.
        """
        with self._lock:
            decision = self._last_decision
            text = "" if decision is None else (decision.text or "").strip()
            return {
                "available": bool(text),
                "decision": None if not text or decision is None else decision.kind.value,
                "text": text or None,
            }

    def friend_proposals_snapshot(self) -> tuple[dict[str, object], ...]:
        """Return local proposal metadata, never the incoming chat transcript."""
        return self.friendships.snapshot()

    def register_confirmed_friend(self, *, alias: str) -> FriendCandidate:
        """Store a manually confirmed existing friend; never contacts Last Z."""
        result = self.friendships.register_confirmed_friend(alias)
        self._record("friendship", "A manually verified friend was registered locally.")
        return result

    def update_friend_proposal(
        self,
        candidate_id: str,
        action: str,
        *,
        alias: str = "",
    ) -> FriendCandidate:
        """Record a human-completed social step; this cannot contact Last Z."""
        if action == "offer-sent":
            result = self.friendships.mark_offer_sent(candidate_id)
        elif action == "friend-added":
            result = self.friendships.mark_friend_added(candidate_id)
        elif action == "confirm-alias":
            result = self.friendships.confirm_alias(candidate_id, alias)
        elif action == "approve-auto-reply":
            result = self.friendships.set_auto_reply_approval(candidate_id, True)
        elif action == "revoke-auto-reply":
            result = self.friendships.set_auto_reply_approval(candidate_id, False)
        elif action == "remove":
            result = self.friendships.remove_friend(candidate_id)
        elif action == "defer":
            result = self.friendships.defer(candidate_id)
        elif action == "block":
            result = self.friendships.block(candidate_id)
        else:
            raise ValueError("Unknown friend-proposal action.")
        self._record("friendship", f"Friend proposal updated locally: {result.status.value}.")
        return result

    def close(self) -> None:
        """Zatrzymaj PEvę bez wpływu na zewnętrzny serwer modelu."""
        self.stop_actions("PEva was closed")
        self.stop_eyes()
        self.stop_model()
        self._session_history.clear()
        self._conversation_flow.reset("PEva was closed")
        self._last_history_turn_count = 0
        self._running = False
        self._record("runtime", "PEva stopped.")

    def _eye_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.poll_eyes_once()
            except Exception as error:
                self._record("eyes_error", f"{type(error).__name__}: {error}")
            remaining = self._interval_seconds - (time.monotonic() - started)
            if remaining > 0:
                self._stop_event.wait(remaining)

    def _stop_android_helper(self) -> None:
        reader = self._reader
        coordinator = getattr(reader, "coordinator", None)
        ocr = getattr(coordinator, "ocr", None)
        stop = getattr(ocr, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def _record(self, category: str, detail: str) -> None:
        with self._lock:
            self._events.append(RuntimeEvent(int(time.time() * 1000), category, str(detail)))


def vision_from_packet(packet: ChatPacket) -> VisionResult:
    """Zamień efemeryczną paczkę OCR na kontrakt Oczu bez zgadywania autora."""
    messages: list[MessageObservation] = []
    text = packet.text.strip()
    animated_emote = is_last_z_animated_emote_text(text)
    confidence = 0.95 if packet.complete else 0.70
    header = parse_private_header(packet.conversation_header, dedicated_header=True)
    if text and not animated_emote:
        messages.append(MessageObservation(
            text,
            header.display_name if header is not None else None,
            min(confidence, header.confidence) if header is not None else confidence,
            "private_chat_local_ocr_header" if header is not None else "private_chat_local_ocr",
        ))
    if packet.contains_nontext_signal or animated_emote:
        messages.append(MessageObservation(None, None, 1.0, "private_chat_visual", nontext=True))
    event = "private_packet_complete" if packet.complete else "private_packet_partial"
    return VisionResult("PRIVATE_CONVERSATION", confidence, tuple(messages), event)

