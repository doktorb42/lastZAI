"""Deterministic, inspectable state of the part of Last Z PEva may observe."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping


KNOWN_SCREENS = frozenset({
    "HQ_BASE",
    "WORLD_MAP",
    "ALLIANCE_CHAT",
    "PRIVATE_CHAT",
    "PRIVATE_CONVERSATION",
    "CHAT_OPEN_UNCLASSIFIED",
})


@dataclass(slots=True)
class WorldState:
    screen: str = "UNKNOWN"
    screen_confidence: float | None = None
    screen_alternate: str | None = None
    chat_shell_evidence: bool = False
    last_known_screen: str | None = None
    last_observed_at: float | None = None
    updated_at: float = 0.0

    in_hq: bool = False
    world_map_open: bool = False
    chat_open: bool = False
    private_chat_open: bool = False
    alliance_chat_open: bool = False

    unread_private: bool = False
    unread_alliance: bool = False

    current_conversation_uid: str | None = None
    current_conversation_room_id: str | None = None
    current_conversation_verified: bool = False
    current_conversation_verified_at: float | None = None
    current_goal_id: str | None = None

    last_event_type: str | None = None
    last_action: str | None = None
    last_action_at: float | None = None
    last_action_result: str | None = None

    game_busy: bool = False
    waiting_for_user: bool = False

    capture_running: bool = False
    ai_available: bool = False
    adb_connected: bool = False

    last_chat_message_at: float | None = None
    last_private_message_at: float | None = None
    last_alliance_message_at: float | None = None
    last_private_reply_at: float | None = None
    last_alliance_reply_at: float | None = None
    active_social_channel: str | None = None
    last_social_decision: str | None = None
    pending_command_id: str | None = None
    pending_command_type: str | None = None
    pending_command_status: str | None = None

    def update_screen(
        self,
        screen: str | None,
        *,
        confidence: float | None = None,
        alternate: str | None = None,
        chat_shell_evidence: bool | None = None,
        unread_private: bool | None = None,
        unread_alliance: bool | None = None,
        updated_at: float | None = None,
    ) -> bool:
        """Apply detector output and return whether an observable field changed."""
        normalized = str(screen or "UNKNOWN").strip().upper() or "UNKNOWN"
        now = time.time() if updated_at is None else float(updated_at)
        before = (
            self.screen,
            self.screen_confidence,
            self.screen_alternate,
            self.chat_shell_evidence,
            self.unread_private,
            self.unread_alliance,
        )

        self.screen = normalized
        self.screen_confidence = confidence
        self.screen_alternate = str(alternate) if alternate else None
        if chat_shell_evidence is not None:
            self.chat_shell_evidence = bool(chat_shell_evidence)
        elif normalized not in {"UNKNOWN", "CHAT_OPEN_UNCLASSIFIED"}:
            self.chat_shell_evidence = normalized in {
                "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION",
            }
        if normalized in KNOWN_SCREENS and normalized != "CHAT_OPEN_UNCLASSIFIED":
            self.last_known_screen = normalized
        self.last_observed_at = now
        self.in_hq = normalized == "HQ_BASE"
        self.world_map_open = normalized == "WORLD_MAP"
        self.chat_open = normalized in {
            "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION", "CHAT_OPEN_UNCLASSIFIED",
        }
        self.private_chat_open = normalized in {
            "PRIVATE_CHAT", "PRIVATE_CONVERSATION",
        }
        self.alliance_chat_open = normalized == "ALLIANCE_CHAT"
        if normalized != "PRIVATE_CONVERSATION":
            self.clear_current_conversation_verification()
        if unread_private is not None:
            self.unread_private = bool(unread_private)
        if unread_alliance is not None:
            self.unread_alliance = bool(unread_alliance)
        self.updated_at = now

        after = (
            self.screen,
            self.screen_confidence,
            self.screen_alternate,
            self.chat_shell_evidence,
            self.unread_private,
            self.unread_alliance,
        )
        return before != after

    def apply_event(self, event: Any) -> bool:
        """Apply one unified event. No LLM is involved in state transitions."""
        event_type = getattr(event, "event_type", None)
        event_name = str(getattr(event_type, "value", event_type or "UNKNOWN"))
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, Mapping):
            payload = {}
        created_at = float(getattr(event, "created_at", time.time()))
        self.last_event_type = event_name

        if event_name == "SCREEN_CHANGED":
            return self.update_screen(
                payload.get("screen"),
                confidence=_as_optional_float(payload.get("confidence")),
                alternate=_as_optional_text(payload.get("alternate")),
                chat_shell_evidence=_as_optional_bool(payload.get("chat_shell_evidence")),
                unread_private=_as_optional_bool(payload.get("unread_private")),
                unread_alliance=_as_optional_bool(payload.get("unread_alliance")),
                updated_at=created_at,
            )

        if event_name == "CHAT_MESSAGE":
            message = payload.get("message", {})
            if isinstance(message, Mapping):
                # An inbound push can arrive while another PM is open.  It is
                # never evidence of the currently displayed conversation.
                group = str(message.get("group") or "")
                self.active_social_channel = group or None
                if group == "custom":
                    self.last_private_message_at = created_at
                elif group == "alliance":
                    self.last_alliance_message_at = created_at
            self.last_chat_message_at = created_at
            self.updated_at = created_at
            return True

        if event_name == "SOCIAL_DECISION":
            self.active_social_channel = str(payload.get("group") or "") or None
            self.last_social_decision = str(payload.get("decision") or "") or None
            self.updated_at = created_at
            return True

        if event_name in {"ACTION_COMPLETED", "ACTION_FAILED"}:
            self.record_action(
                str(payload.get("action") or "unknown"),
                str(payload.get("status") or event_name.lower()),
                at=created_at,
            )
            self.game_busy = False
            return True

        if event_name == "USER_DECISION_REQUIRED":
            self.waiting_for_user = True
            self.updated_at = created_at
            return True

        if event_name == "USER_DECISION_RECEIVED":
            self.waiting_for_user = False
            self.updated_at = created_at
            return True

        if event_name == "SYSTEM_START":
            self.capture_running = bool(payload.get("capture_running", True))
            self.ai_available = bool(payload.get("ai_available", self.ai_available))
            self.adb_connected = bool(payload.get("adb_connected", self.adb_connected))
            self.updated_at = created_at
            return True

        if event_name == "SYSTEM_STOP":
            self.capture_running = False
            self.game_busy = False
            self.updated_at = created_at
            return True

        self.updated_at = created_at
        return False

    def record_action(
        self,
        action: str,
        result: str,
        *,
        at: float | None = None,
    ) -> None:
        self.last_action = str(action)
        self.last_action_result = str(result)
        self.last_action_at = time.time() if at is None else float(at)
        self.updated_at = self.last_action_at

    def set_current_conversation_verified(
        self,
        *,
        uid: str,
        room_id: str,
        verified_at: float | None = None,
    ) -> None:
        self.current_conversation_uid = str(uid)
        self.current_conversation_room_id = str(room_id)
        self.current_conversation_verified = True
        self.current_conversation_verified_at = (
            time.time() if verified_at is None else float(verified_at)
        )
        self.updated_at = self.current_conversation_verified_at

    def clear_current_conversation_verification(self) -> None:
        self.current_conversation_uid = None
        self.current_conversation_room_id = None
        self.current_conversation_verified = False
        self.current_conversation_verified_at = None

    def snapshot(self) -> dict[str, Any]:
        """Return primitives only, suitable for logs and future datasets."""
        return asdict(self)

    def is_safe_for_navigation(self) -> bool:
        """Fail closed for unknown, busy, or disconnected UI state.

        A pending user decision is deliberately not global state here: capture,
        analysis and unrelated work must continue while a delivery is pending.
        """
        return (
            self.adb_connected
            and not self.game_busy
            and self.screen in KNOWN_SCREENS
            and (
                self.screen != "CHAT_OPEN_UNCLASSIFIED"
                or self.chat_shell_evidence
            )
        )


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

