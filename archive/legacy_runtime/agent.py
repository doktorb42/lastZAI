from __future__ import annotations

import argparse
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mozg.conversation_store import ConversationStore
from mozg.lastz_ai import AIReply, LastZAI
from config.runtime_config import (
    CONVERSATION_DB_PATH,
    DATA_DIR,
    SELF_UID,
    is_direct_private_room,
)


DEFAULT_OWN_UID = SELF_UID
DEFAULT_DB_PATH = CONVERSATION_DB_PATH


@dataclass(slots=True)
class AgentConfig:
    own_uid: str = DEFAULT_OWN_UID

    recent_history_limit: int = 20
    memory_limit: int = 12
    min_memory_importance: float = 0.25

    # For now the agent prepares a reply but DOES NOT send it.
    auto_send: bool = False

    # Current step handles private messages only.
    allowed_groups: set[str] = field(
        default_factory=lambda: {"custom"}
    )


@dataclass(slots=True)
class PendingReply:
    reply_id: str

    room_id: str
    player_uid: str
    player_name: str | None

    incoming_source_key: str
    incoming_text: str

    text: str

    created_at: int

    ai_latency_ms: float | None = None
    ai_model: str | None = None

    status: str = "pending"


@dataclass(slots=True)
class AgentResult:
    status: str
    message: str

    pending_reply: PendingReply | None = None
    ai_reply: AIReply | None = None

    @property
    def reply_ready(self) -> bool:
        return (
            self.pending_reply is not None
            and self.status == "reply_ready"
        )


class LastZAgent:
    """
    Central logic layer between:

        chat_capture.py
             ↓
        conversation_store.py
             ↓
        lastz_ai.py
             ↓
        agent.py
             ↓
        later: action_ex.py / adb_controll.py

    IMPORTANT:
    At this stage the agent DOES NOT send anything to Last Z.
    It only:
      1. receives a ChatMessage,
      2. ignores duplicates / unsupported messages,
      3. loads old player context,
      4. stores the incoming message,
      5. asks the local AI for a reply,
      6. keeps the reply in a pending queue.

    The outgoing message is written to conversation.db only after
    mark_reply_sent() is called by the later action/executor layer.
    """

    def __init__(
        self,
        *,
        ai: LastZAI | None = None,
        store: ConversationStore | None = None,
        config: AgentConfig | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ):
        self.config = config or AgentConfig()

        self.ai = ai or LastZAI()

        self._owns_store = store is None
        self.store = store or ConversationStore(
            db_path
        )

        self._pending: dict[str, PendingReply] = {}

        # Kept only so the old main.py does not immediately break before
        # we replace it in step 7.
        self.pending_private_message = False

    # ==================================================================
    # LIFECYCLE
    # ==================================================================

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> "LastZAgent":
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ) -> None:
        self.close()

    # ==================================================================
    # NEW CHAT PIPELINE
    # ==================================================================

    def process_chat_message(
        self,
        message: Any,
        *,
        social_context: list[str] | None = None,
    ) -> AgentResult:
        """
        Main step-4 entry point.

        Expected input:
            chat_capture.ChatMessage
        """

        validation = self._validate_message(
            message
        )

        if validation is not None:
            return validation

        room_id = str(
            message.room_id
        )
        sender_uid = str(
            message.sender_uid
        )
        source_key = str(
            message.source_message_key
        )
        incoming_text = str(
            message.text or ""
        ).strip()

        # ----------------------------------------------------------
        # DUPLICATE CHECK
        # ----------------------------------------------------------

        if self.store.has_source_message_key(
            source_key
        ):
            return AgentResult(
                status="duplicate",
                message=(
                    "Message already exists in conversation.db: "
                    f"{source_key}"
                ),
            )

        # ----------------------------------------------------------
        # LOAD CONTEXT BEFORE INSERTING CURRENT MESSAGE
        #
        # This is intentional. Otherwise current user message would be:
        #   - in history
        #   - AND incoming_text
        # so the model would see it twice.
        # ----------------------------------------------------------

        old_context = self.store.get_ai_context(
            player_uid=sender_uid,
            room_id=room_id,
            recent_history_limit=(
                self.config.recent_history_limit
            ),
            memory_limit=(
                self.config.memory_limit
            ),
            min_memory_importance=(
                self.config.min_memory_importance
            ),
        )

        # ----------------------------------------------------------
        # STORE INCOMING
        # ----------------------------------------------------------

        inserted = self.store.save_incoming(
            message
        )

        if not inserted:
            return AgentResult(
                status="duplicate",
                message=(
                    "Incoming message was not inserted because "
                    "it already exists."
                ),
            )

        # ----------------------------------------------------------
        # BUILD USEFUL LONG-TERM CONTEXT
        # ----------------------------------------------------------

        extra_context: list[str] = list(social_context or [])

        if old_context.summary:
            extra_context.append(
                "Previous conversation summary: "
                + old_context.summary
            )

        if old_context.memories:
            extra_context.append(
                "Known long-term facts about this player:"
            )

            extra_context.extend(
                old_context.memories
            )

        # ----------------------------------------------------------
        # ASK AI
        # ----------------------------------------------------------

        ai_reply = self.ai.generate_reply_safe(
            incoming_text,
            sender_name=getattr(
                message,
                "sender_name",
                None,
            ),
            sender_uid=sender_uid,
            sender_alliance=getattr(
                message,
                "sender_alliance",
                None,
            ),
            sender_server_id=getattr(
                message,
                "sender_server_id",
                None,
            ),
            sender_language=getattr(
                message,
                "original_lang",
                None,
            ),
            room_id=room_id,
            group=getattr(
                message,
                "group",
                "custom",
            ),
            history=old_context.history,
            extra_context=extra_context,
        )

        if ai_reply.error:
            return AgentResult(
                status="ai_error",
                message=ai_reply.error,
                ai_reply=ai_reply,
            )

        if ai_reply.empty:
            return AgentResult(
                status="empty_reply",
                message=(
                    "AI returned an empty reply."
                ),
                ai_reply=ai_reply,
            )

        # ----------------------------------------------------------
        # PENDING REPLY
        #
        # Do NOT save as outgoing yet.
        # We have not physically sent anything to Last Z.
        # ----------------------------------------------------------

        pending = PendingReply(
            reply_id=uuid.uuid4().hex,

            room_id=room_id,
            player_uid=sender_uid,
            player_name=getattr(
                message,
                "sender_name",
                None,
            ),

            incoming_source_key=source_key,
            incoming_text=incoming_text,

            text=ai_reply.text,

            created_at=self._now_ms(),

            ai_latency_ms=ai_reply.latency_ms,
            ai_model=ai_reply.model,
        )

        self._pending[
            pending.reply_id
        ] = pending

        return AgentResult(
            status="reply_ready",
            message=(
                "AI reply generated and waiting for send confirmation."
            ),
            pending_reply=pending,
            ai_reply=ai_reply,
        )

    def generate_social_reply(
        self,
        message: Any,
        *,
        history: list[Any] | None = None,
        extra_context: list[str] | None = None,
    ) -> AIReply:
        """Generate a preview candidate for alliance chat without a send path.

        Unlike ``process_chat_message`` this does not create PendingReply,
        persist an outgoing row, or expose an alliance action.  PlayerLoop
        deliberately keeps alliance output preview-only in Stage 2.
        """
        incoming_text = str(getattr(message, "text", "") or "").strip()
        if not incoming_text:
            return AIReply(text="")
        return self.ai.generate_reply_safe(
            incoming_text,
            sender_name=getattr(message, "sender_name", None),
            sender_uid=str(getattr(message, "sender_uid", "") or ""),
            sender_alliance=getattr(message, "sender_alliance", None),
            sender_server_id=getattr(message, "sender_server_id", None),
            sender_language=getattr(message, "original_lang", None),
            room_id=str(getattr(message, "room_id", "") or ""),
            group=str(getattr(message, "group", "alliance") or "alliance"),
            history=history or [],
            extra_context=extra_context or [],
        )

    def record_incoming_message(self, message: Any) -> AgentResult:
        """Persist a validated PM without asking the legacy reply generator.

        Agency Core decides the intent first.  This small entry point keeps the
        durable incoming-message and duplicate semantics of ``LastZAgent``
        while ensuring that a no-reply Agency decision never invokes the old
        mandatory text generator.
        """
        validation = self._validate_message(message)
        if validation is not None:
            return validation
        source_key = str(getattr(message, "source_message_key", "") or "")
        if source_key and self.store.has_source_message_key(source_key):
            return AgentResult(status="duplicate", message=f"Message already exists in conversation.db: {source_key}")
        if not self.store.save_incoming(message):
            return AgentResult(status="duplicate", message="Incoming message was not inserted because it already exists.")
        return AgentResult(status="recorded", message="Incoming message recorded for Agency decision.")

    def create_pending_reply(self, message: Any, text: str, *, record_incoming: bool = True) -> AgentResult:
        """Create a verified-send pending reply from Agency-selected text only.

        It never sends, normalizes, rewrites, or claims delivery.  The caller
        still has to pass the existing ActionClaimGuard and navigation route.
        """
        validation = self._validate_message(message)
        if validation is not None:
            return validation
        reply = str(text or "").strip()
        if not reply:
            return AgentResult(status="empty_reply", message="Agency selected an empty reply.")
        source_key = str(getattr(message, "source_message_key", "") or "")
        if record_incoming:
            recorded = self.record_incoming_message(message)
            if recorded.status == "duplicate":
                return recorded
            if recorded.status != "recorded":
                return recorded
        pending = PendingReply(
            reply_id=uuid.uuid4().hex,
            room_id=str(getattr(message, "room_id", "") or ""),
            player_uid=str(getattr(message, "sender_uid", "") or ""),
            player_name=getattr(message, "sender_name", None),
            incoming_source_key=source_key,
            incoming_text=str(getattr(message, "text", "") or "").strip(),
            text=reply,
            created_at=self._now_ms(),
            ai_model="agency_core",
        )
        self._pending[pending.reply_id] = pending
        return AgentResult(status="reply_ready", message="Agency reply is waiting for verified send confirmation.", pending_reply=pending)

    # ==================================================================
    # PENDING REPLY QUEUE
    # ==================================================================

    def queue_proactive_reply(
        self,
        *,
        player_uid: str,
        room_id: str,
        text: str,
        player_name: str | None = None,
    ) -> PendingReply:
        """Queue a reviewed proactive text for the existing verified send path.

        This creates no transport action and writes no outgoing database row.
        ``mark_reply_sent`` remains the only persistence point, after the
        same verified server echo used by ordinary private replies.
        """
        uid = str(player_uid or "").strip()
        room = str(room_id or "").strip()
        # Agency Core has already validated the final conversational text.
        # Do not run a second personality/style normalizer before delivery.
        final_text = " ".join(str(text or "").split()).strip()
        if not uid or not room or not final_text:
            raise ValueError("Proactive reply needs UID, verified room, and text.")
        if not is_direct_private_room(room, self_uid=self.config.own_uid, peer_uid=uid):
            raise ValueError("Proactive reply room is not an exact direct private room.")
        pending = PendingReply(
            reply_id=uuid.uuid4().hex,
            room_id=room,
            player_uid=uid,
            player_name=player_name,
            incoming_source_key=f"proactive:{uuid.uuid4().hex}",
            incoming_text="",
            text=final_text,
            created_at=self._now_ms(),
        )
        self._pending[pending.reply_id] = pending
        return pending

    def get_pending_reply(
        self,
        reply_id: str,
    ) -> PendingReply | None:
        return self._pending.get(
            str(reply_id)
        )

    def list_pending_replies(
        self,
    ) -> list[PendingReply]:
        return sorted(
            self._pending.values(),
            key=lambda item: item.created_at,
        )

    def discard_reply(
        self,
        reply_id: str,
    ) -> bool:
        pending = self._pending.pop(
            str(reply_id),
            None,
        )

        if pending is None:
            return False

        pending.status = "discarded"
        return True

    def replace_reply_text(
        self,
        reply_id: str,
        text: str,
    ) -> bool:
        """
        Allows manual edit before sending.
        """

        pending = self.get_pending_reply(
            reply_id
        )

        if pending is None:
            return False

        text = str(
            text or ""
        ).strip()

        if not text:
            raise ValueError(
                "Reply text cannot be empty."
            )

        pending.text = self.ai.normalize_reply(
            text
        )

        return True

    def mark_reply_sent(
        self,
        reply_id: str,
        *,
        sender_uid: str | None = None,
        sender_name: str = "PEva",
        server_time: int | None = None,
        send_time: int | None = None,
    ) -> int:
        """
        Called later ONLY after action_ex/adb_controll confirms sending.

        This is when the assistant reply becomes permanent conversation
        history.
        """

        reply_id = str(
            reply_id
        )

        pending = self._pending.get(
            reply_id
        )

        if pending is None:
            raise KeyError(
                f"Unknown pending reply: {reply_id}"
            )

        if pending.status != "pending":
            raise RuntimeError(
                f"Reply is not pending: {pending.status}"
            )

        outgoing_id = self.store.save_outgoing(
            room_id=pending.room_id,
            player_uid=pending.player_uid,
            text=pending.text,
            sender_uid=(
                sender_uid
                or self.config.own_uid
            ),
            sender_name=sender_name,
            server_time=server_time,
            send_time=send_time,
        )

        pending.status = "sent"

        del self._pending[
            reply_id
        ]

        return outgoing_id

    # ==================================================================
    # MEMORY HELPERS
    #
    # These do NOT automatically extract memories yet.
    # They give agent.py a clean interface for step 9.
    # ==================================================================

    def remember_player_fact(
        self,
        *,
        player_uid: str,
        memory_type: str,
        content: str,
        memory_key: str | None = None,
        importance: float = 0.5,
        confidence: float = 0.5,
        source_message_id: int | None = None,
    ) -> int:
        return self.store.add_memory(
            player_uid=player_uid,
            memory_type=memory_type,
            content=content,
            memory_key=memory_key,
            importance=importance,
            confidence=confidence,
            source_message_id=source_message_id,
        )

    # ==================================================================
    # VALIDATION
    # ==================================================================

    def _validate_message(
        self,
        message: Any,
    ) -> AgentResult | None:

        if message is None:
            return AgentResult(
                status="ignored",
                message="Message is None.",
            )

        group = str(
            getattr(
                message,
                "group",
                "",
            )
            or ""
        )

        if (
            self.config.allowed_groups
            and group not in self.config.allowed_groups
        ):
            return AgentResult(
                status="ignored_group",
                message=(
                    f"Unsupported chat group: {group!r}"
                ),
            )

        sender_uid = str(
            getattr(
                message,
                "sender_uid",
                "",
            )
            or ""
        )

        if not sender_uid:
            return AgentResult(
                status="invalid_message",
                message="Missing sender UID.",
            )

        if sender_uid == str(
            self.config.own_uid
        ):
            return AgentResult(
                status="ignored_self",
                message="Ignoring own message.",
            )

        room_id = str(
            getattr(
                message,
                "room_id",
                "",
            )
            or ""
        )

        if not room_id:
            return AgentResult(
                status="invalid_message",
                message="Missing room ID.",
            )

        # Defense in depth: even if a caller bypasses ChatCapture, public or
        # language custom rooms must never reach the private-reply pipeline.
        if (
            group == "custom"
            and not is_direct_private_room(
                room_id,
                self_uid=self.config.own_uid,
                peer_uid=sender_uid,
            )
        ):
            return AgentResult(
                status="ignored_non_private_room",
                message=(
                    "Custom room is not a verified direct PM for "
                    "self UID + sender UID."
                ),
            )

        text = str(
            getattr(
                message,
                "text",
                "",
            )
            or ""
        ).strip()

        if not text:
            return AgentResult(
                status="ignored_empty",
                message="Ignoring empty message.",
            )

        source_key = getattr(
            message,
            "source_message_key",
            None,
        )

        if not source_key:
            return AgentResult(
                status="invalid_message",
                message=(
                    "Missing source_message_key."
                ),
            )

        if not self.ai.should_reply(
            text,
            sender_uid=sender_uid,
            own_uid=self.config.own_uid,
            group=group,
        ):
            return AgentResult(
                status="ignored",
                message=(
                    "AI reply filter rejected the message."
                ),
            )

        return None

    # ==================================================================
    # LEGACY SCREEN DECISION
    #
    # Temporary compatibility with the current main.py.
    # We will replace this logic when we reach steps 5-7.
    # ==================================================================

    def decide(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Old screen-navigation API kept temporarily so replacing agent.py
        does not break the current main.py.

        New incoming chat handling must use process_chat_message().
        """

        screen = state.get(
            "screen",
            "UNKNOWN",
        )

        unread = state.get(
            "unread",
            {},
        )

        chat_unread = bool(
            unread.get(
                "chat",
                False,
            )
        )

        private_unread = bool(
            unread.get(
                "private",
                False,
            )
        )

        alliance_unread = bool(
            unread.get(
                "alliance",
                False,
            )
        )

        print(
            f"[AGENT-LEGACY] screen={screen} | "
            f"chat={chat_unread} | "
            f"private={private_unread} | "
            f"alliance={alliance_unread} | "
            f"pending={self.pending_private_message}"
        )

        if (
            screen in {
                "HQ_BASE",
                "WORLD_MAP",
            }
            and chat_unread
        ):
            return {
                "action": "open_chat"
            }

        if private_unread:
            self.pending_private_message = True

        if not self.pending_private_message:
            return {
                "action": "wait"
            }

        if screen == "UNKNOWN":
            return {
                "action": "wait"
            }

        if screen == "ALLIANCE_CHAT":
            return {
                "action": "open_private_chat"
            }

        if screen == "PRIVATE_CHAT":
            return {
                "action": "open_private_person",
                "index": 0,
            }

        if screen == "PRIVATE_CONVERSATION":
            self.pending_private_message = False

            return {
                "action": "wait"
            }

        return {
            "action": "wait"
        }

    # ==================================================================
    # HELPERS
    # ==================================================================

    @staticmethod
    def _now_ms() -> int:
        return int(
            time.time() * 1000
        )


# ======================================================================
# SELF TEST - NO LLAMA SERVER REQUIRED
# ======================================================================


class _FakeAI:
    """
    Tiny fake model used only by `py agent.py --self-test`.
    """

    def should_reply(
        self,
        incoming_text: str,
        **kwargs: Any,
    ) -> bool:
        return bool(
            str(incoming_text).strip()
        )

    def generate_reply_safe(
        self,
        incoming_text: str,
        **kwargs: Any,
    ) -> AIReply:
        return AIReply(
            text=(
                "Test reply to: "
                + str(incoming_text)
            ),
            model="fake-ai",
            latency_ms=1.0,
        )

    @staticmethod
    def normalize_reply(
        text: str,
    ) -> str:
        return " ".join(
            str(text).split()
        )


def _self_test() -> None:
    from archive.legacy_runtime.chat_capture import ChatMessage

    test_db = DATA_DIR / "test-artifacts" / "agent_self_test.db"
    test_db.parent.mkdir(parents=True, exist_ok=True)

    for candidate in (
        test_db,
        Path(str(test_db) + "-wal"),
        Path(str(test_db) + "-shm"),
    ):
        if candidate.exists():
            candidate.unlink()

    test_peer_uid = "9000000000000002"

    message = ChatMessage(
        room_id=(
            f"custom_{test_peer_uid}_{SELF_UID}_v2"
        ),
        group="custom",
        sender_uid=test_peer_uid,
        sender_name="OG doktorb",
        text="hello agent",
        seq_id=999,
        server_time=1791930738885,
        send_time=1791930738,
        sender_alliance="ROXY",
        sender_server_id=772,
        message_type=0,
        post_type=0,
        original_lang="en",
        pushseq="test-pushseq",
        raw_data={
            "msg": "hello agent",
        },
    )

    with ConversationStore(
        test_db
    ) as store:
        agent = LastZAgent(
            ai=_FakeAI(),  # type: ignore[arg-type]
            store=store,
        )

        result = agent.process_chat_message(
            message
        )

        print(
            "Status:",
            result.status,
        )

        if not result.reply_ready:
            raise RuntimeError(
                f"Expected reply_ready, got {result}"
            )

        pending = result.pending_reply

        assert pending is not None

        print(
            "Incoming:",
            pending.incoming_text,
        )
        print(
            "AI reply:",
            pending.text,
        )
        print(
            "Pending replies:",
            len(
                agent.list_pending_replies()
            ),
        )

        # Incoming exists now, assistant reply does not yet.
        print(
            "Messages before confirmation:",
            store.message_count(
                room_id=message.room_id
            ),
        )

        outgoing_id = agent.mark_reply_sent(
            pending.reply_id
        )

        print(
            "Outgoing stored ID:",
            outgoing_id,
        )

        print(
            "Messages after confirmation:",
            store.message_count(
                room_id=message.room_id
            ),
        )

        duplicate = agent.process_chat_message(
            message
        )

        print(
            "Duplicate status:",
            duplicate.status,
        )

    print()
    print("SELF-TEST OK")


# ======================================================================
# LIVE PREVIEW - REAL CHAT + REAL LLM, NO SENDING
# ======================================================================


def _live_preview(
    *,
    clear_logcat: bool,
) -> None:
    from archive.legacy_runtime.chat_capture import ChatCapture

    ai = LastZAI()

    print("=" * 64)
    print("LAST Z AGENT - LIVE PREVIEW")
    print("=" * 64)
    print("LLM:", ai.config.base_url)
    print("Health:", ai.health_check())
    print("Mode: PREVIEW ONLY - NOTHING WILL BE SENT")
    print()

    if not ai.health_check():
        raise SystemExit(
            "Local llama-server is not running."
        )

    capture = ChatCapture()

    with LastZAgent(
        ai=ai,
        db_path=DEFAULT_DB_PATH,
    ) as agent:

        try:
            capture.start(
                clear_logcat=clear_logcat
            )

            print(
                "Waiting for private messages... "
                "Ctrl+C to stop."
            )
            print()

            for message in capture.iter_messages():
                print(
                    f"[IN] {message.sender_name or message.sender_uid}: "
                    f"{message.text}"
                )

                result = agent.process_chat_message(
                    message
                )

                print(
                    "[AGENT]",
                    result.status,
                    "-",
                    result.message,
                )

                if result.pending_reply is not None:
                    pending = result.pending_reply

                    print(
                        f"[AI PREVIEW] {pending.text}"
                    )
                    print(
                        f"[REPLY ID] {pending.reply_id}"
                    )
                    print(
                        "[INFO] Not sent to Last Z."
                    )

                print()

        except KeyboardInterrupt:
            print()
            print("[INFO] Stopping live preview...")

        finally:
            capture.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Last Z agent logic test / live preview."
        )
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "Test agent/store logic without BlueStacks "
            "or llama-server."
        ),
    )

    mode.add_argument(
        "--live-preview",
        action="store_true",
        help=(
            "Receive real PMs and generate real AI replies, "
            "but do not send them."
        ),
    )

    parser.add_argument(
        "--clear-logcat",
        action="store_true",
        help=(
            "Clear Android logcat before live preview."
        ),
    )

    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

    if args.live_preview:
        _live_preview(
            clear_logcat=args.clear_logcat
        )
        return


if __name__ == "__main__":
    main()
