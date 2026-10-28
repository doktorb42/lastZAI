"""Small, payload-safe log formatters for PEva chat message flow.

The runtime deliberately logs only the message content and the identifiers
needed to trace it. Raw push payloads, prompts, and transport internals stay
out of these operational log lines.
"""

from __future__ import annotations

from typing import Any


def _one_line(value: Any, *, missing: str = "-") -> str:
    """Render a value safely in one log line without losing message content."""
    if value is None:
        return missing
    text = str(value)
    if not text:
        return missing
    return (
        text.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _message_text(value: Any) -> str:
    return _one_line(value, missing="")


def incoming_message(*, sender: Any, uid: Any, room_id: Any, seq_id: Any, text: Any) -> str:
    return (
        f"[INCOMING] sender={_one_line(sender)} uid={_one_line(uid)} "
        f"room={_one_line(room_id)} seq={_one_line(seq_id)} "
        f'text="{_message_text(text)}"'
    )


def ai_reply(*, goal_id: Any, reply_id: Any, text: Any) -> str:
    return (
        f"[AI REPLY] goal={_one_line(goal_id)} reply_id={_one_line(reply_id)} "
        f'text="{_message_text(text)}"'
    )


def ai_original(*, text: Any) -> str:
    return f'[AI ORIGINAL] text="{_message_text(text)}"'


def ai_final(*, source: Any, text: Any) -> str:
    return f"[AI FINAL] source={_one_line(source)} text=\"{_message_text(text)}\""


def send_attempt(*, goal_id: Any, reply_id: Any, target_uid: Any, room_id: Any, text: Any) -> str:
    return _send_line(
        "SEND ATTEMPT",
        goal_id=goal_id,
        reply_id=reply_id,
        target_uid=target_uid,
        room_id=room_id,
        text=text,
    )


def send_confirmed(
    *, goal_id: Any, reply_id: Any, target_uid: Any, room_id: Any, text: Any, seq_id: Any
) -> str:
    return (
        _send_line(
            "SEND CONFIRMED",
            goal_id=goal_id,
            reply_id=reply_id,
            target_uid=target_uid,
            room_id=room_id,
            text=text,
        )
        + f" seq={_one_line(seq_id)}"
    )


def send_failed(
    *, goal_id: Any, reply_id: Any, target_uid: Any, room_id: Any, text: Any, reason: Any
) -> str:
    return (
        _send_line(
            "SEND FAILED",
            goal_id=goal_id,
            reply_id=reply_id,
            target_uid=target_uid,
            room_id=room_id,
            text=text,
        )
        + f' reason="{_message_text(reason)}"'
    )


def _send_line(
    label: str,
    *,
    goal_id: Any,
    reply_id: Any,
    target_uid: Any,
    room_id: Any,
    text: Any,
) -> str:
    return (
        f"[{label}] goal={_one_line(goal_id)} reply_id={_one_line(reply_id)} "
        f"uid={_one_line(target_uid)} room={_one_line(room_id)} "
        f'text="{_message_text(text)}"'
    )
