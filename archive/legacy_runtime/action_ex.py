from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from archive.legacy_runtime import adb_controll
from rece.delivery_state import DeliveryState, delivery_metadata, initial_delivery_metadata
from archive.legacy_runtime.message_logging import send_attempt, send_confirmed, send_failed
from config.runtime_config import MAX_MESSAGE_CHARS, SELF_UID, is_direct_private_room


DEFAULT_MAX_MESSAGE_CHARS = MAX_MESSAGE_CHARS


@dataclass(slots=True)
class ActionPolicy:
    """
    Deterministic safety rules for actions.

    AI text is never allowed to choose arbitrary screen coordinates.
    It can only request one of the explicit actions supported here.
    """

    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS

    # Generic send_message is kept only for compatibility with old code.
    # The AI pipeline should use reply_private instead.
    allow_legacy_send_message: bool = False

    allowed_person_indexes: tuple[int, ...] = tuple(range(7))


@dataclass(slots=True)
class ActionResult:
    ok: bool
    status: str
    message: str
    action: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        prefix = "Success" if self.ok else "[BLOCKED]"
        return f"{prefix} {self.message}".strip()


class ActionExecutor:
    """
    Safe action layer between agent.py and adb_controll.py.

    Responsibilities:
    - validate action shape,
    - validate the current game screen,
    - enforce message limits,
    - block arbitrary/unknown actions,
    - NEVER accept raw x/y coordinates from AI,
    - later hand verified private-reply actions to adb_controll.py.

    Step 5 deliberately does not implement the UID navigation itself.
    That belongs to step 6: adb_controll.py.
    """

    def __init__(
        self,
        policy: ActionPolicy | None = None,
        *,
        logger: Callable[[str], None] = print,
    ):
        self.policy = policy or ActionPolicy()
        self.log = logger

    # ==================================================================
    # PUBLIC API
    # ==================================================================

    def execute(
        self,
        action: Mapping[str, Any] | None,
        state: Mapping[str, Any] | None,
    ) -> ActionResult:

        if not isinstance(action, Mapping):
            return self._blocked(
                "invalid_action",
                "Action must be a mapping/dict.",
            )

        if not isinstance(state, Mapping):
            return self._blocked(
                "invalid_state",
                "State must be a mapping/dict.",
            )

        name = action.get("action")

        if not isinstance(name, str):
            return self._blocked(
                "invalid_action",
                "Action name is missing or invalid.",
            )

        name = name.strip()

        if not name:
            return self._blocked(
                "invalid_action",
                "Action name is empty.",
            )

        screen = str(
            state.get(
                "screen",
                "UNKNOWN",
            )
        )

        self.log(
            f"[EXECUTOR] action={name} "
            f"screen={screen}"
        )

        handler = getattr(
            self,
            f"_action_{name}",
            None,
        )

        if handler is None:
            return self._blocked(
                "unknown_action",
                f"Unknown action: {name}",
                action=name,
            )

        try:
            result = handler(
                action,
                state,
            )

        except Exception as exc:
            result = self._blocked(
                "execution_error",
                (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                action=name,
            )
        else:
            if not isinstance(
                result,
                ActionResult,
            ):
                result = self._blocked(
                    "executor_error",
                    (
                        f"Action handler {name!r} "
                        "returned invalid result."
                    ),
                    action=name,
                )

        if name == "reply_private" and not result.ok:
            self._log_private_reply_failure(action, result)

        return result

    # ==================================================================
    # PURE VALIDATION
    # ==================================================================

    def validate_private_reply(
        self,
        action: Mapping[str, Any],
    ) -> ActionResult:
        """
        Validate the contract produced by agent.py.

        This check does not click anything.
        """

        reply_id = self._clean_required_string(
            action.get("reply_id")
        )

        target_uid = self._clean_required_string(
            action.get("target_uid")
        )

        room_id = self._clean_required_string(
            action.get("room_id")
        )

        text = action.get("text")

        if reply_id is None:
            return self._blocked(
                "invalid_reply",
                "reply_private requires reply_id.",
                action="reply_private",
            )

        if target_uid is None:
            return self._blocked(
                "invalid_reply",
                "reply_private requires target_uid.",
                action="reply_private",
            )

        if room_id is None:
            return self._blocked(
                "invalid_reply",
                "reply_private requires room_id.",
                action="reply_private",
            )

        if not isinstance(text, str):
            return self._blocked(
                "invalid_reply",
                "reply_private text must be a string.",
                action="reply_private",
            )

        text = text.strip()

        if not text:
            return self._blocked(
                "invalid_reply",
                "reply_private text is empty.",
                action="reply_private",
            )

        if len(text) > self.policy.max_message_chars:
            return self._blocked(
                "message_too_long",
                (
                    "Message too long: "
                    f"{len(text)}/"
                    f"{self.policy.max_message_chars}"
                ),
                action="reply_private",
            )

        # Exact room contract: a private room must contain exactly PEva UID
        # and the requested target UID. Substring matching is not sufficient.
        if not is_direct_private_room(
            room_id,
            self_uid=SELF_UID,
            peer_uid=target_uid,
        ):
            return self._blocked(
                "target_room_mismatch",
                (
                    "room_id is not a verified direct PM for "
                    "self UID + target UID."
                ),
                action="reply_private",
            )

        return ActionResult(
            ok=True,
            status="validated",
            message="Private reply action is valid.",
            action="reply_private",
            data={
                "reply_id": reply_id,
                "target_uid": target_uid,
                "room_id": room_id,
                "text": text,
            },
        )

    # ==================================================================
    # ACTIONS
    # ==================================================================

    def _action_wait(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:
        return ActionResult(
            ok=True,
            status="waiting",
            message="Waiting",
            action="wait",
        )

    def _action_open_chat(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:

        screen = str(
            state.get(
                "screen",
                "UNKNOWN",
            )
        )

        if screen not in {
            "HQ_BASE",
            "WORLD_MAP",
        }:
            return self._blocked(
                "wrong_screen",
                f"Cannot open chat from {screen}.",
                action="open_chat",
            )

        if not adb_controll.open_chat():
            return self._blocked(
                "open_chat_failed",
                "ADB could not tap the chat button.",
                action="open_chat",
            )

        return ActionResult(
            ok=True,
            status="executed",
            message="Chat opened.",
            action="open_chat",
        )

    def _action_open_private_chat(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:

        screen = str(
            state.get(
                "screen",
                "UNKNOWN",
            )
        )

        if screen == "PRIVATE_CHAT":
            return self._blocked(
                "already_open",
                "Already in private chat.",
                action="open_private_chat",
            )

        chat_shell_safe = bool(state.get("chat_shell_evidence"))
        if screen not in {"ALLIANCE_CHAT", "CHAT_OPEN_UNCLASSIFIED"}:
            return self._blocked(
                "wrong_screen",
                (
                    "Cannot open private chat "
                    f"from {screen}."
                ),
                action="open_private_chat",
            )
        if screen == "CHAT_OPEN_UNCLASSIFIED" and not chat_shell_safe:
            return self._blocked(
                "chat_shell_unverified",
                "Cannot open the private tab without verified chat-shell evidence.",
                action="open_private_chat",
            )

        if not adb_controll.open_private_chat():
            return self._blocked(
                "open_private_chat_failed",
                "ADB could not tap the private-chat tab.",
                action="open_private_chat",
            )

        return ActionResult(
            ok=True,
            status="executed",
            message="Private chat opened.",
            action="open_private_chat",
        )

    def _action_open_alliance_chat(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:

        screen = str(
            state.get(
                "screen",
                "UNKNOWN",
            )
        )

        if screen == "ALLIANCE_CHAT":
            return self._blocked(
                "already_open",
                "Already in alliance chat.",
                action="open_alliance_chat",
            )

        # Kept disabled intentionally until the later action plan needs it.
        return self._blocked(
            "disabled",
            "open_alliance_chat is disabled for now.",
            action="open_alliance_chat",
        )

    def _action_open_private_person(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:
        """
        Legacy index-based navigation.

        Step 6 will replace this for AI replies with UID-verified navigation.
        """

        screen = str(
            state.get(
                "screen",
                "UNKNOWN",
            )
        )

        if screen != "PRIVATE_CHAT":
            return self._blocked(
                "wrong_screen",
                (
                    "You must be in PRIVATE_CHAT "
                    "to select a person."
                ),
                action="open_private_person",
            )

        index = action.get("index")

        if not isinstance(index, int):
            return self._blocked(
                "invalid_index",
                "Person index must be an integer.",
                action="open_private_person",
            )

        if index not in self.policy.allowed_person_indexes:
            return self._blocked(
                "invalid_index",
                (
                    "Person index must be one of: "
                    + ", ".join(
                        str(value)
                        for value
                        in self.policy.allowed_person_indexes
                    )
                ),
                action="open_private_person",
            )

        adb_controll.select_person(
            index
        )

        return ActionResult(
            ok=True,
            status="executed",
            message=(
                f"Opened private person index {index}."
            ),
            action="open_private_person",
            data={
                "index": index,
            },
        )

    def _action_send_message(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:
        """
        Old unverified send action.

        It is deliberately disabled for the new AI pipeline because it knows
        only "a private conversation is open", not WHO is open.
        """

        if not self.policy.allow_legacy_send_message:
            return self._blocked(
                "unsafe_legacy_send",
                (
                    "Generic send_message is disabled. "
                    "Use reply_private with a target UID."
                ),
                action="send_message",
            )

        screen = str(
            state.get(
                "screen",
                "UNKNOWN",
            )
        )

        if screen != "PRIVATE_CONVERSATION":
            return self._blocked(
                "wrong_screen",
                (
                    "Messages can only be sent "
                    "inside PRIVATE_CONVERSATION."
                ),
                action="send_message",
            )

        text = action.get(
            "text",
            "",
        )

        if not isinstance(text, str):
            return self._blocked(
                "invalid_message",
                "Message must be a string.",
                action="send_message",
            )

        text = text.strip()

        if not text:
            return self._blocked(
                "invalid_message",
                "Message is empty.",
                action="send_message",
            )

        if len(text) > self.policy.max_message_chars:
            return self._blocked(
                "message_too_long",
                (
                    f"Message too long: "
                    f"{len(text)}/"
                    f"{self.policy.max_message_chars}"
                ),
                action="send_message",
            )

        sent = adb_controll.write_message(
            text
        )

        if sent is False:
            return self._blocked(
                "send_failed",
                "adb_controll.write_message returned False.",
                action="send_message",
            )

        return ActionResult(
            ok=True,
            status="executed",
            message="Message sent.",
            action="send_message",
        )

    def _action_reply_private(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:
        """
        New safe action contract.

        Step 5 validates everything, but physical UID navigation is intentionally
        deferred until step 6.

        When adb_controll.py gains:
            send_private_reply_verified(...)

        this action will call it.
        """

        validated = self.validate_private_reply(
            action
        )

        if not validated.ok:
            return validated

        screen = str(state.get("screen") or "UNKNOWN").strip().upper()
        target_is_current = (
            screen == "PRIVATE_CONVERSATION"
            and bool(state.get("current_conversation_verified"))
            and str(state.get("current_conversation_uid") or "") == validated.data["target_uid"]
            and str(state.get("current_conversation_room_id") or "") == validated.data["room_id"]
        )
        if not target_is_current:
            metadata = initial_delivery_metadata(
                reason=f"private_chat_not_ready:{screen}"
            )
            metadata["delivery_state"] = DeliveryState.FAILED_SAFE.value
            return self._blocked(
                "target_not_verified",
                "Physical private delivery requires the verified target conversation.",
                action="reply_private",
                data={**validated.data, **metadata, "action_id": action.get("action_id")},
            )

        # Step 6 capability check.
        sender = getattr(
            adb_controll,
            "send_private_reply_verified",
            None,
        )

        if not callable(sender):
            metadata = initial_delivery_metadata(reason="verified_transport_unavailable")
            metadata["delivery_state"] = DeliveryState.FAILED_SAFE.value
            return self._blocked(
                "transport_not_ready",
                "UID-verified private reply transport is unavailable.",
                action="reply_private",
                data={**validated.data, **metadata, "action_id": action.get("action_id")},
            )

        attempt_logged = False

        def log_send_attempt() -> None:
            nonlocal attempt_logged
            if attempt_logged:
                return
            attempt_logged = True
            self.log(
                send_attempt(
                    goal_id=action.get("goal_id"),
                    reply_id=validated.data["reply_id"],
                    target_uid=validated.data["target_uid"],
                    room_id=validated.data["room_id"],
                    text=validated.data["text"],
                )
            )

        self.log(
            "[DELIVERY PREPARE] "
            f"action={action.get('action_id') or '-'} "
            f"screen={state.get('screen') or 'UNKNOWN'}"
        )
        self.log(
            "[NAVIGATION] "
            f"action={action.get('action_id') or '-'} "
            f"screen={state.get('screen') or 'UNKNOWN'} "
            f"current_verified={str(bool(state.get('current_conversation_verified'))).lower()} "
            f"target_uid={validated.data['target_uid']} room={validated.data['room_id']}"
        )

        outcome = sender(
            target_uid=validated.data["target_uid"],
            room_id=validated.data["room_id"],
            text=validated.data["text"],
            screen=str(state.get("screen") or "UNKNOWN"),
            current_conversation_verified=bool(state.get("current_conversation_verified")),
            current_conversation_uid=state.get("current_conversation_uid"),
            current_conversation_room_id=state.get("current_conversation_room_id"),
            on_send_attempt=log_send_attempt,
        )
        metadata = delivery_metadata(outcome if isinstance(outcome, Mapping) else None)
        if not isinstance(outcome, Mapping):
            metadata.update({
                "delivery_state": DeliveryState.FAILED_SAFE.value,
                "reason": "verified_transport_returned_no_metadata",
            })
        metadata["action_id"] = action.get("action_id")
        # Compatibility for test/future transport adapters that can only
        # report a completed tap.  The built-in verified transport uses the
        # callback immediately before the first physical text-entry tap.
        if metadata["send_tap_attempted"] and not attempt_logged:
            log_send_attempt()
        self.log(
            "[NAVIGATION] "
            f"action={action.get('action_id') or '-'} "
            f"verified={str(bool(metadata['navigation_verified'])).lower()} "
            f"target_verified={str(bool(metadata['target_uid_verified'])).lower()} "
            f"path={metadata.get('navigation_path') or '-'} reason={metadata['reason']}"
        )
        self.log(
            "[DELIVERY] "
            f"action={action.get('action_id') or '-'} "
            f"state={metadata['delivery_state']} "
            f"send_tap={str(bool(metadata['send_tap_attempted'])).lower()} "
            f"echo={str(bool(metadata['server_echo_seen'])).lower()} "
            f"reason={metadata['reason']}"
        )

        # A successful tap is not confirmation. Only the matching own server
        # echo may complete a reply_private action.
        if not (
            metadata["delivery_state"] == DeliveryState.CONFIRMED.value
            and bool(metadata["server_echo_seen"])
            and isinstance(outcome, Mapping)
            and outcome.get("confirmed") is True
        ):
            status = (
                "delivery_ambiguous"
                if metadata["delivery_state"] == DeliveryState.AMBIGUOUS.value
                else "delivery_failed_safe"
            )
            return self._blocked(
                status,
                f"Private reply delivery ended as {metadata['delivery_state']}: {metadata['reason']}",
                action="reply_private",
                data={**validated.data, **metadata},
            )

        self.log(
            send_confirmed(
                goal_id=action.get("goal_id"),
                reply_id=validated.data["reply_id"],
                target_uid=validated.data["target_uid"],
                room_id=validated.data["room_id"],
                text=validated.data["text"],
                seq_id=metadata.get("server_echo_seq"),
            )
        )

        return ActionResult(
            ok=True,
            status="sent",
            message=(
                "UID-verified private reply was sent."
            ),
            action="reply_private",
            data={
                **validated.data,
                **metadata,
            },
        )

    def _action_prepare_private_reply_target(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:
        """Select and prove the target PM; deliberately never sends text."""
        validated = self.validate_private_reply(action)
        if not validated.ok:
            return validated

        navigator = getattr(adb_controll, "navigate_private_reply_target_verified", None)
        if not callable(navigator):
            metadata = initial_delivery_metadata(reason="verified_target_navigation_unavailable")
            metadata["delivery_state"] = DeliveryState.FAILED_SAFE.value
            return self._blocked(
                "transport_not_ready",
                "UID-verified private target navigation is unavailable.",
                action="prepare_private_reply_target",
                data={**validated.data, **metadata, "action_id": action.get("action_id")},
            )

        self.log(
            "[NAVIGATION] "
            f"action={action.get('action_id') or '-'} "
            f"state={state.get('screen') or 'UNKNOWN'} step=verify_target "
            f"uid={validated.data['target_uid']}"
        )
        outcome = navigator(
            target_uid=validated.data["target_uid"],
            room_id=validated.data["room_id"],
            screen=str(state.get("screen") or "UNKNOWN"),
            current_conversation_verified=bool(state.get("current_conversation_verified")),
            current_conversation_uid=state.get("current_conversation_uid"),
            current_conversation_room_id=state.get("current_conversation_room_id"),
        )
        metadata = delivery_metadata(outcome if isinstance(outcome, Mapping) else None)
        if not isinstance(outcome, Mapping):
            metadata.update({
                "delivery_state": DeliveryState.FAILED_SAFE.value,
                "reason": "verified_target_navigation_returned_no_metadata",
            })
        else:
            for key in (
                "current_conversation_uid",
                "current_conversation_room_id",
                "current_conversation_verified",
                "navigation_path",
                "person_index",
            ):
                if key in outcome:
                    metadata[key] = outcome[key]
        metadata["action_id"] = action.get("action_id")
        if not (metadata["navigation_verified"] and metadata["target_uid_verified"]):
            return self._blocked(
                "target_navigation_failed",
                f"Private target navigation failed: {metadata['reason']}",
                action="prepare_private_reply_target",
                data={**validated.data, **metadata},
            )

        self.log(
            "[NAVIGATION] "
            f"action={action.get('action_id') or '-'} "
            "state=PRIVATE_CONVERSATION verified=true "
            f"uid={validated.data['target_uid']}"
        )
        return ActionResult(
            ok=True,
            status="target_verified",
            message="UID-verified private conversation is ready for delivery.",
            action="prepare_private_reply_target",
            data={**validated.data, **metadata},
        )

    def _action_back(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:

        screen = str(
            state.get(
                "screen",
                "UNKNOWN",
            )
        )

        if screen not in {
            "ALLIANCE_CHAT",
            "PRIVATE_CHAT",
            "PRIVATE_CONVERSATION",
        }:
            return self._blocked(
                "wrong_screen",
                (
                    "Back action is allowed only "
                    "inside the chat UI."
                ),
                action="back",
            )

        if not adb_controll.back():
            return self._blocked(
                "back_failed",
                "ADB could not perform Back.",
                action="back",
            )

        return ActionResult(
            ok=True,
            status="executed",
            message="Back pressed.",
            action="back",
        )

    def _action_recover_back(
        self,
        action: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> ActionResult:
        """Controlled system-Back for a proven chat-shell/unknown recovery."""
        screen = str(state.get("screen", "UNKNOWN")).upper()
        last_known = str(state.get("last_known_screen", "")).upper()
        chat_screens = {
            "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION", "CHAT_OPEN_UNCLASSIFIED",
        }
        if not (
            screen in chat_screens
            or (
                screen == "UNKNOWN"
                and (
                    bool(state.get("chat_shell_evidence"))
                    or last_known in chat_screens
                )
            )
        ):
            return self._blocked(
                "unsafe_recovery_back",
                "Controlled Back requires current or last-known chat-shell evidence.",
                action="recover_back",
            )
        if not adb_controll.back():
            return self._blocked(
                "recover_back_failed",
                "ADB could not perform controlled recovery Back.",
                action="recover_back",
            )
        return ActionResult(
            ok=True,
            status="executed",
            message="Controlled recovery Back pressed.",
            action="recover_back",
        )

    # ==================================================================
    # HELPERS
    # ==================================================================

    def _log_private_reply_failure(
        self,
        action: Mapping[str, Any],
        result: ActionResult,
    ) -> None:
        """Emit one audit line for every failed or inconclusive PM send."""
        reason = result.status
        if result.message:
            reason = f"{reason}: {result.message}"
        self.log(
            send_failed(
                goal_id=action.get("goal_id"),
                reply_id=action.get("reply_id"),
                target_uid=action.get("target_uid"),
                room_id=action.get("room_id"),
                text=action.get("text"),
                reason=reason,
            )
        )

    @staticmethod
    def _clean_required_string(
        value: Any,
    ) -> str | None:

        if not isinstance(value, str):
            return None

        value = value.strip()

        return value or None

    @staticmethod
    def _blocked(
        status: str,
        message: str,
        *,
        action: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> ActionResult:

        return ActionResult(
            ok=False,
            status=status,
            message=message,
            action=action,
            data=data or {},
        )


# ======================================================================
# ACTION BUILDERS
# ======================================================================


def build_private_reply_action(
    pending_reply: Any,
) -> dict[str, Any]:
    """
    Convert agent.PendingReply into the only action shape that is allowed
    to send an AI-generated private reply.

    The model itself never constructs coordinates.
    """

    reply_id = getattr(
        pending_reply,
        "reply_id",
        None,
    )
    target_uid = getattr(
        pending_reply,
        "player_uid",
        None,
    )
    room_id = getattr(
        pending_reply,
        "room_id",
        None,
    )
    text = getattr(
        pending_reply,
        "text",
        None,
    )

    return {
        "action": "reply_private",
        "reply_id": reply_id,
        "target_uid": target_uid,
        "room_id": room_id,
        "text": text,
    }


# ======================================================================
# BACKWARD COMPATIBILITY FOR CURRENT main.py
# ======================================================================


_DEFAULT_EXECUTOR = ActionExecutor()


def execute_action_result(
    action: Mapping[str, Any] | None,
    state: Mapping[str, Any] | None,
) -> ActionResult:
    """
    New API.
    """
    return _DEFAULT_EXECUTOR.execute(
        action,
        state,
    )


def execute_action(
    action: Mapping[str, Any] | None,
    state: Mapping[str, Any] | None,
) -> str:
    """
    Old API used by the current main.py.

    Keeping it returning text means step 5 can be dropped into the project
    without changing main.py yet.
    """

    return str(
        execute_action_result(
            action,
            state,
        )
    )


# ======================================================================
# SELF TEST
# ======================================================================


@dataclass(slots=True)
class _FakePendingReply:
    reply_id: str
    player_uid: str
    room_id: str
    text: str


def _self_test() -> None:
    executor = ActionExecutor()

    pending = _FakePendingReply(
        reply_id="reply-123",
        player_uid="1349515331000772",
        room_id=(
            "custom_1349515331000772_"
            f"{SELF_UID}_v2"
        ),
        text="Hey bro",
    )

    action = build_private_reply_action(
        pending
    )

    print(
        "Built action:",
        action,
    )

    validation = executor.validate_private_reply(
        action
    )

    print(
        "Validation:",
        validation.status,
        "-",
        validation.message,
    )

    if not validation.ok:
        raise RuntimeError(
            "Expected valid reply_private action."
        )

    wrong_target = dict(
        action
    )
    wrong_target[
        "target_uid"
    ] = "999999999"

    blocked = executor.validate_private_reply(
        wrong_target
    )

    print(
        "Wrong target:",
        blocked.status,
        "-",
        blocked.message,
    )

    if blocked.ok:
        raise RuntimeError(
            "Expected wrong target to be blocked."
        )

    too_long = dict(
        action
    )
    too_long["text"] = (
        "x"
        * (
            executor.policy.max_message_chars
            + 1
        )
    )

    blocked_length = (
        executor.validate_private_reply(
            too_long
        )
    )

    print(
        "Too long:",
        blocked_length.status,
        "-",
        blocked_length.message,
    )

    if blocked_length.ok:
        raise RuntimeError(
            "Expected long message to be blocked."
        )

    # Never touch real ADB from a unit/self-test. Mock the transport while
    # exercising the same executor path used in production.
    original_sender = getattr(
        adb_controll,
        "send_private_reply_verified",
        None,
    )

    def _fake_sender(**kwargs: Any) -> dict[str, Any]:
        return {
            "confirmed": True,
            "mock": True,
            "seq_id": 1,
            "server_echo_seq": 1,
            "server_echo_seen": True,
            "send_tap_attempted": True,
            "delivery_state": "CONFIRMED",
            **kwargs,
        }

    adb_controll.send_private_reply_verified = _fake_sender

    try:
        result = executor.execute(
            action,
            {
                "screen": "PRIVATE_CONVERSATION",
                "current_conversation_verified": True,
                "current_conversation_uid": pending.player_uid,
                "current_conversation_room_id": pending.room_id,
            },
        )
    finally:
        if original_sender is None:
            delattr(
                adb_controll,
                "send_private_reply_verified",
            )
        else:
            adb_controll.send_private_reply_verified = original_sender

    print(
        "Mock transport execution:",
        result.status,
        "-",
        result.message,
    )

    if not result.ok or result.status != "sent":
        raise RuntimeError(
            "Expected mocked reply_private send to succeed."
        )

    # Generic AI-style send is blocked by default.
    legacy = executor.execute(
        {
            "action": "send_message",
            "text": "unsafe test",
        },
        {
            "screen": "PRIVATE_CONVERSATION",
        },
    )

    print(
        "Legacy send:",
        legacy.status,
        "-",
        legacy.message,
    )

    if legacy.ok:
        raise RuntimeError(
            "Legacy generic send should be disabled."
        )

    print()
    print("SELF-TEST OK")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Last Z action executor."
        )
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
