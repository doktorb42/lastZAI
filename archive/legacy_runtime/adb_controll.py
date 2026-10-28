from __future__ import annotations

import argparse
import base64
import json
import random
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from archive.legacy_runtime.coordinates import (
    WRITE_CHAT,
    SEND_TEXT,
    CHAT_BUTTON,
    BACK_BUTTON,
    PRIVATE_PERSON_X,
    PRIVATE_PERSON_FIRST_Y,
    PRIVATE_PERSON_STEP_Y,
    PRIVATE_PERSON_VISIBLE_COUNT,
    ALLIANCE_CHAT_TAB,
    PRIVATE_CHAT_TAB,
)
from rece.delivery_state import DeliveryState, initial_delivery_metadata
from config.runtime_config import (
    ADB_DEVICE as DEVICE,
    ADB_PATH,
    PROJECT_DIR,
    SCREENSHOT_DIR,
    SELF_UID,
    is_direct_private_room,
)


# ======================================================================
# CONFIG
# ======================================================================

SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PRIVATE_CLICK_RE = re.compile(
    r"OnClickRoomItemPRIVATE_(\d+)_to_(\d+)"
)

# ``push.chat.up.room`` is the current Last Z name for the same callback
# payload formerly sent as ``push.chat.room``.  Keep both for an update-safe
# server-echo check after PEva sends a PM.
CHAT_PUSH_COMMANDS = frozenset({
    "push.chat.room",
    "push.chat.up.room",
})



# ======================================================================
# DATA MODELS
# ======================================================================


@dataclass(slots=True)
class PrivateClickEvent:
    uid_a: str
    uid_b: str
    target_uid: str | None
    self_uid: str | None
    raw_line: str

    def involves(self, uid: str) -> bool:
        uid = str(uid)
        return uid == self.uid_a or uid == self.uid_b


@dataclass(slots=True)
class PrivateFindResult:
    found: bool
    target_uid: str
    index: int | None = None
    event: PrivateClickEvent | None = None
    reason: str | None = None


@dataclass(slots=True)
class ChatEcho:
    room_id: str
    sender_uid: str
    text: str
    seq_id: int | None
    server_time: int | None
    raw_payload: dict[str, Any]


# ======================================================================
# LOW-LEVEL ADB
# ======================================================================


def adb(
    *args: str,
    capture_output: bool = True,
    file=None,
    text: bool = False,
    timeout: float | None = 15.0,
):
    command = [
        ADB_PATH,
        "-s",
        DEVICE,
        *args,
    ]

    # subprocess.run(capture_output=True) already creates BOTH
    # stdout=PIPE and stderr=PIPE internally. Passing stderr=PIPE at the
    # same time raises:
    #   ValueError: stdout and stderr arguments may not be used
    #   with capture_output.
    kwargs: dict[str, Any] = {
        "timeout": timeout,
    }

    if file is not None:
        # Binary commands such as `exec-out screencap -p`.
        kwargs["stdout"] = file
        kwargs["stderr"] = subprocess.PIPE
        kwargs["text"] = False
    elif capture_output:
        kwargs["capture_output"] = True
        kwargs["text"] = text

        # Windows would otherwise decode ADB/logcat output with the local
        # console code page (for example cp1250). Unity/logcat can contain
        # UTF-8 bytes that are invalid in cp1250, which caused repeated
        # UnicodeDecodeError exceptions during UID polling.
        if text:
            kwargs["encoding"] = "utf-8"
            kwargs["errors"] = "replace"
    else:
        kwargs["capture_output"] = False
        kwargs["text"] = text

        if text:
            kwargs["encoding"] = "utf-8"
            kwargs["errors"] = "replace"

    return subprocess.run(
        command,
        **kwargs,
    )


def check_connection() -> bool:
    try:
        result = subprocess.run(
            [
                ADB_PATH,
                "-s",
                DEVICE,
                "get-state",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (
        OSError,
        subprocess.SubprocessError,
    ):
        return False

    return (
        result.returncode == 0
        and result.stdout.strip() == "device"
    )


def get_screen_size() -> tuple[int, int] | None:
    """Return the effective Android display size reported by ``wm size``."""
    try:
        result = adb(
            "shell",
            "wm",
            "size",
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    text = str(result.stdout or "")

    # If an override exists it appears after Physical size and is the one
    # Android input/screencap uses, so prefer the last reported size.
    matches = re.findall(
        r"(?:Physical|Override) size:\s*(\d+)x(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    if not matches:
        return None

    width, height = matches[-1]
    return int(width), int(height)


def get_screencap_size() -> tuple[int, int] | None:
    """
    Return the pixel size of the CURRENT rotated screencap.

    BlueStacks / Android may report ``wm size`` in the display's natural
    orientation (for example 1600x900) while a portrait-locked game and
    ADB input coordinates operate in the rotated space (900x1600).

    PNG stores width/height in the IHDR header, so this check needs no
    Pillow/OpenCV dependency.
    """
    try:
        result = adb(
            "exec-out",
            "screencap",
            "-p",
            capture_output=True,
            text=False,
            timeout=15.0,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    raw = result.stdout

    if not isinstance(raw, (bytes, bytearray)):
        return None

    # PNG signature + IHDR:
    # bytes 16..19 = width, 20..23 = height, big-endian.
    if (
        len(raw) < 24
        or raw[:8] != b"\x89PNG\r\n\x1a\n"
    ):
        return None

    width = int.from_bytes(
        raw[16:20],
        "big",
    )
    height = int.from_bytes(
        raw[20:24],
        "big",
    )

    if width <= 0 or height <= 0:
        return None

    return width, height


# ======================================================================
# SCREEN / TOUCH
# ======================================================================


def screenshot(
    filename: str = "current.png",
):
    path = SCREENSHOT_DIR / filename

    with open(path, "wb") as file:
        result = adb(
            "exec-out",
            "screencap",
            "-p",
            file=file,
            timeout=15.0,
        )

    if result.returncode != 0:
        error = (
            result.stderr.decode(
                errors="ignore"
            )
            if isinstance(
                result.stderr,
                bytes,
            )
            else str(result.stderr or "")
        )

        raise RuntimeError(
            error or "screencap failed"
        )

    return path


def tap(
    x: int,
    y: int,
) -> bool:
    result = adb(
        "shell",
        "input",
        "tap",
        str(int(x)),
        str(int(y)),
    )

    return result.returncode == 0


def swipe(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration: int = 300,
) -> bool:
    result = adb(
        "shell",
        "input",
        "swipe",
        str(int(x1)),
        str(int(y1)),
        str(int(x2)),
        str(int(y2)),
        str(int(duration)),
    )

    return result.returncode == 0


# ======================================================================
# TEXT INPUT
# ======================================================================


def input_text(
    text: str,
    min_delay: float = 0.01,
    max_delay: float = 0.15,
) -> bool:
    """
    Send UTF-8 text through ADBKeyboard one character at a time.

    The existing project already uses ADB_INPUT_B64, so this keeps the
    behavior that worked for spaces/punctuation and non-ASCII characters.
    """

    if not isinstance(text, str):
        return False

    if not text:
        return False

    for char in text:
        encoded = base64.b64encode(
            char.encode("utf-8")
        ).decode("ascii")

        result = adb(
            "shell",
            "am",
            "broadcast",
            "-a",
            "ADB_INPUT_B64",
            "--es",
            "msg",
            encoded,
        )

        if result.returncode != 0:
            return False

        delay = random.uniform(
            min_delay,
            max_delay,
        )

        if char in ".!?":
            delay += random.uniform(
                0.2,
                0.3,
            )
        elif char in ",;:":
            delay += random.uniform(
                0.02,
                0.4,
            )

        time.sleep(delay)

    return True


# ======================================================================
# OLD UI HELPERS - PRESERVED
# ======================================================================


def open_chat() -> bool:
    return tap(
        *CHAT_BUTTON
    )


def open_private_chat() -> bool:
    return tap(
        *PRIVATE_CHAT_TAB
    )


def open_alliance_chat() -> bool:
    return tap(
        *ALLIANCE_CHAT_TAB
    )


def back() -> bool:
    return tap(
        *BACK_BUTTON
    )


def send_message() -> bool:
    return tap(
        *SEND_TEXT
    )


def select_person(
    index: int = 0,
) -> bool:
    if (
        not isinstance(index, int)
        or index < 0
        or index >= PRIVATE_PERSON_VISIBLE_COUNT
    ):
        return False

    y = (
        PRIVATE_PERSON_FIRST_Y
        + (
            index
            * PRIVATE_PERSON_STEP_Y
        )
    )

    return tap(
        PRIVATE_PERSON_X,
        y,
    )


def write_message(
    text: str,
) -> bool:
    """
    Legacy write helper.

    It knows nothing about the currently opened player.
    action_ex.py therefore does NOT use this for AI replies.
    """

    if not isinstance(text, str):
        return False

    text = text.strip()

    if not text:
        return False

    if not tap(
        *WRITE_CHAT
    ):
        return False

    time.sleep(0.2)

    if not input_text(
        text
    ):
        return False

    time.sleep(0.1)

    return send_message()


# ======================================================================
# LOGCAT HELPERS
# ======================================================================


def _dump_unity_logcat() -> list[str]:
    """
    Read the current Unity logcat buffer.

    threadtime includes a timestamp, so two identical clicks still produce
    distinct raw lines. This lets us detect only events generated AFTER a tap.
    """

    result = adb(
        "logcat",
        "-d",
        "-v",
        "threadtime",
        "Unity:I",
        "*:S",
        text=True,
        timeout=10.0,
    )

    if result.returncode != 0:
        return []

    return (
        result.stdout
        or ""
    ).splitlines()


def parse_private_click_event(
    line: str,
    *,
    self_uid: str = SELF_UID,
) -> PrivateClickEvent | None:
    match = PRIVATE_CLICK_RE.search(
        line
    )

    if match is None:
        return None

    uid_a = match.group(1)
    uid_b = match.group(2)

    target_uid: str | None
    detected_self: str | None

    if uid_a == self_uid:
        detected_self = uid_a
        target_uid = uid_b
    elif uid_b == self_uid:
        detected_self = uid_b
        target_uid = uid_a
    else:
        detected_self = None
        target_uid = None

    return PrivateClickEvent(
        uid_a=uid_a,
        uid_b=uid_b,
        target_uid=target_uid,
        self_uid=detected_self,
        raw_line=line,
    )


def get_private_click_events(
    *,
    self_uid: str = SELF_UID,
) -> list[PrivateClickEvent]:
    events: list[
        PrivateClickEvent
    ] = []

    for line in _dump_unity_logcat():
        event = parse_private_click_event(
            line,
            self_uid=self_uid,
        )

        if event is not None:
            events.append(
                event
            )

    return events


def get_latest_private_click_event(
    *,
    self_uid: str = SELF_UID,
) -> PrivateClickEvent | None:
    events = get_private_click_events(
        self_uid=self_uid
    )

    if not events:
        return None

    return events[-1]


def _wait_for_new_private_click(
    baseline_lines: set[str],
    *,
    self_uid: str,
    timeout: float = 2.0,
    poll_interval: float = 0.10,
) -> PrivateClickEvent | None:

    deadline = (
        time.monotonic()
        + timeout
    )

    while (
        time.monotonic()
        < deadline
    ):
        events = get_private_click_events(
            self_uid=self_uid
        )

        new_events = [
            event
            for event in events
            if event.raw_line
            not in baseline_lines
        ]

        if new_events:
            return new_events[-1]

        time.sleep(
            poll_interval
        )

    return None


# ======================================================================
# PRIVATE ROOM / TARGET VALIDATION
# ======================================================================


def validate_private_room(
    room_id: str,
    target_uid: str,
    *,
    self_uid: str = SELF_UID,
) -> bool:
    """Validate the direct-PM room with the shared runtime contract."""
    return is_direct_private_room(
        room_id,
        self_uid=self_uid,
        peer_uid=target_uid,
    )


def event_matches_target(
    event: PrivateClickEvent | None,
    target_uid: str,
    *,
    self_uid: str = SELF_UID,
) -> bool:
    if event is None:
        return False

    return (
        event.self_uid
        == str(self_uid)
        and event.target_uid
        == str(target_uid)
    )


# ======================================================================
# UID-VERIFIED PRIVATE CHAT SELECTION
# ======================================================================


def click_private_person_verified(
    index: int,
    *,
    self_uid: str = SELF_UID,
    timeout: float = 2.0,
) -> PrivateClickEvent | None:
    """
    Click one visible PM row and return the NEW logcat event caused by it.

    If no new OnClickRoomItemPRIVATE event appears, return None.
    """

    baseline = {
        event.raw_line
        for event
        in get_private_click_events(
            self_uid=self_uid
        )
    }

    if not select_person(
        index
    ):
        return None

    return _wait_for_new_private_click(
        baseline,
        self_uid=self_uid,
        timeout=timeout,
    )


def find_private_conversation_visible(
    target_uid: str,
    *,
    self_uid: str = SELF_UID,
    click_timeout: float = 2.0,
    back_delay: float = 0.35,
) -> PrivateFindResult:
    """
    Search the currently visible PM rows.

    Safety behavior:
    - every row is identified from Last Z's own logcat event,
    - after a mismatch we return to the PM list,
    - if a click produces no UID event, scanning stops instead of guessing,
    - if the target is found, its conversation remains open.

    Current step intentionally scans only the 7 visible rows. Scrolling will
    be added only after we have validated the basic UID flow in real use.
    """

    target_uid = str(
        target_uid
    ).strip()

    if not target_uid:
        return PrivateFindResult(
            found=False,
            target_uid="",
            reason="empty_target_uid",
        )

    for index in range(
        PRIVATE_PERSON_VISIBLE_COUNT
    ):
        event = click_private_person_verified(
            index,
            self_uid=self_uid,
            timeout=click_timeout,
        )

        if event is None:
            return PrivateFindResult(
                found=False,
                target_uid=target_uid,
                index=index,
                # The row tap may have landed while Unity/logcat was late.
                # The navigation manager will re-observe the actual screen
                # and may allow exactly one bounded retry from a stable list.
                reason="click_not_registered",
            )

        print(
            "[UID CHECK]",
            f"index={index}",
            f"clicked={event.target_uid}",
            f"wanted={target_uid}",
        )

        if event_matches_target(
            event,
            target_uid,
            self_uid=self_uid,
        ):
            return PrivateFindResult(
                found=True,
                target_uid=target_uid,
                index=index,
                event=event,
            )

        # We know a different conversation opened.
        # Go back to the visible PM list before trying the next row.
        if not back():
            return PrivateFindResult(
                found=False,
                target_uid=target_uid,
                index=index,
                event=event,
                reason=(
                    "Could not return to PM list "
                    "after wrong UID."
                ),
            )

        time.sleep(
            back_delay
        )

    return PrivateFindResult(
        found=False,
        target_uid=target_uid,
        reason=(
            "Target UID was not found in the "
            f"{PRIVATE_PERSON_VISIBLE_COUNT} visible PM rows."
        ),
    )


# ======================================================================
# SERVER-ECHO CONFIRMATION
# ======================================================================


def _parse_callback_json(
    line: str,
) -> dict[str, Any] | None:
    """
    Patched Last Z log lines contain the callback JSON on the same line.
    """

    if not any(command in line for command in CHAT_PUSH_COMMANDS):
        return None

    start = line.find(
        "{"
    )

    if start < 0:
        return None

    candidate = line[
        start:
    ].strip()

    try:
        payload = json.loads(
            candidate
        )
    except json.JSONDecodeError:
        return None

    if not isinstance(
        payload,
        dict,
    ):
        return None

    return payload


def _find_matching_own_echo(
    lines: list[str],
    *,
    baseline_lines: set[str],
    room_id: str,
    self_uid: str,
    text: str,
) -> ChatEcho | None:

    for line in lines:
        if line in baseline_lines:
            continue

        payload = _parse_callback_json(
            line
        )

        if payload is None:
            continue

        if payload.get(
            "cmd"
        ) not in CHAT_PUSH_COMMANDS:
            continue

        data = payload.get(
            "data"
        )

        if not isinstance(
            data,
            dict,
        ):
            continue

        if str(
            data.get(
                "roomId",
                "",
            )
        ) != room_id:
            continue

        if str(
            data.get(
                "sender",
                "",
            )
        ) != str(self_uid):
            continue

        if str(
            data.get(
                "msg",
                "",
            )
        ) != text:
            continue

        seq_id = data.get(
            "seqId"
        )
        server_time = data.get(
            "serverTime"
        )

        try:
            seq_id = (
                int(seq_id)
                if seq_id is not None
                else None
            )
        except (
            TypeError,
            ValueError,
        ):
            seq_id = None

        try:
            server_time = (
                int(server_time)
                if server_time is not None
                else None
            )
        except (
            TypeError,
            ValueError,
        ):
            server_time = None

        return ChatEcho(
            room_id=room_id,
            sender_uid=str(
                self_uid
            ),
            text=text,
            seq_id=seq_id,
            server_time=server_time,
            raw_payload=payload,
        )

    return None


def wait_for_own_chat_echo(
    *,
    baseline_lines: set[str],
    room_id: str,
    self_uid: str,
    text: str,
    timeout: float = 3.0,
    poll_interval: float = 0.12,
) -> ChatEcho | None:
    deadline = (
        time.monotonic()
        + timeout
    )

    while (
        time.monotonic()
        < deadline
    ):
        lines = _dump_unity_logcat()

        echo = _find_matching_own_echo(
            lines,
            baseline_lines=baseline_lines,
            room_id=room_id,
            self_uid=self_uid,
            text=text,
        )

        if echo is not None:
            return echo

        time.sleep(
            poll_interval
        )

    return None


# ======================================================================
# SAFE PRIVATE REPLY
# ======================================================================


def send_private_reply_verified(
    *,
    target_uid: str,
    room_id: str,
    text: str,
    self_uid: str = SELF_UID,
    require_server_echo: bool = True,
    screen: str = "PRIVATE_CHAT",
    current_conversation_verified: bool = False,
    current_conversation_uid: str | None = None,
    current_conversation_room_id: str | None = None,
    on_send_attempt: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """
    Safely send one PM reply.

    Return explicit transport facts for every path.  An inbound message never
    establishes current-conversation identity; only the verified navigation
    path or an already verified WorldState may use the direct-send fast path.
    """

    target_uid = str(target_uid).strip()
    room_id = str(room_id).strip()
    text = str(text or "").strip()
    if not text:
        metadata = initial_delivery_metadata()
        metadata.update({"target_uid": target_uid, "room_id": room_id, "text": text})
        return _delivery_failed_safe(metadata, "empty_message")
    metadata = navigate_private_reply_target_verified(
        target_uid=target_uid,
        room_id=room_id,
        self_uid=self_uid,
        screen=screen,
        current_conversation_verified=current_conversation_verified,
        current_conversation_uid=current_conversation_uid,
        current_conversation_room_id=current_conversation_room_id,
    )
    metadata["text"] = text
    if metadata["delivery_state"] == DeliveryState.FAILED_SAFE.value:
        return metadata
    return _send_in_verified_private_conversation(
        metadata,
        target_uid=target_uid,
        room_id=room_id,
        text=text,
        self_uid=self_uid,
        require_server_echo=require_server_echo,
        require_latest_click_binding=(
            metadata.get("navigation_path") != "verified_current_conversation"
        ),
        on_send_attempt=on_send_attempt,
    )


def navigate_private_reply_target_verified(
    *,
    target_uid: str,
    room_id: str,
    self_uid: str = SELF_UID,
    screen: str = "PRIVATE_CHAT",
    current_conversation_verified: bool = False,
    current_conversation_uid: str | None = None,
    current_conversation_room_id: str | None = None,
) -> dict[str, Any]:
    """Verify/select the exact PM target without entering text or tapping Send."""
    target_uid = str(target_uid).strip()
    room_id = str(room_id).strip()
    metadata = initial_delivery_metadata()
    metadata.update({"target_uid": target_uid, "room_id": room_id})

    if not target_uid:
        return _delivery_failed_safe(metadata, "empty_target_uid")
    if not validate_private_room(room_id, target_uid, self_uid=self_uid):
        return _delivery_failed_safe(metadata, "room_id_not_verified")
    metadata["room_verified"] = True

    normalized_screen = str(screen or "UNKNOWN").strip().upper()
    direct_verified = (
        normalized_screen == "PRIVATE_CONVERSATION"
        and bool(current_conversation_verified)
        and str(current_conversation_uid or "") == target_uid
        and str(current_conversation_room_id or "") == room_id
    )
    if direct_verified:
        metadata.update({
            "navigation_verified": True,
            "target_uid_verified": True,
            "current_conversation_uid": target_uid,
            "current_conversation_room_id": room_id,
            "current_conversation_verified": True,
            "navigation_path": "verified_current_conversation",
            "reason": "target_verified",
        })
        return metadata

    if normalized_screen == "PRIVATE_CONVERSATION":
        # Never click PM-list coordinates while inside an unverified or wrong
        # conversation.  First return to the list, then prove the new target.
        if not back():
            return _delivery_failed_safe(metadata, "could_not_leave_unverified_private_conversation")
        metadata["navigation_path"] = "back_then_verified_navigation"
        time.sleep(0.35)
        normalized_screen = "PRIVATE_CHAT"

    if normalized_screen != "PRIVATE_CHAT":
        return _delivery_failed_safe(metadata, f"private_chat_not_ready:{normalized_screen}")

    found = find_private_conversation_visible(target_uid, self_uid=self_uid)
    if not found.found:
        return _delivery_failed_safe(metadata, found.reason or "target_private_conversation_not_found")

    latest = get_latest_private_click_event(self_uid=self_uid)
    if not event_matches_target(latest, target_uid, self_uid=self_uid):
        return _delivery_failed_safe(metadata, "target_uid_not_verified_after_navigation")

    metadata.update({
        "navigation_verified": True,
        "target_uid_verified": True,
        "current_conversation_uid": target_uid,
        "current_conversation_room_id": room_id,
        "current_conversation_verified": True,
        "person_index": found.index,
        "navigation_path": metadata.get("navigation_path", "verified_navigation"),
        "reason": "target_verified",
    })
    return metadata


def _send_in_verified_private_conversation(
    metadata: dict[str, Any],
    *,
    target_uid: str,
    room_id: str,
    text: str,
    self_uid: str,
    require_server_echo: bool,
    require_latest_click_binding: bool,
    on_send_attempt: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Send only after the caller has established the exact PM identity."""
    send_baseline = set(_dump_unity_logcat())
    # This is the first physical text-entry step, after all UID/room checks.
    if on_send_attempt is not None:
        on_send_attempt()
    if not tap(*WRITE_CHAT):
        return _delivery_failed_safe(metadata, "could_not_focus_chat_input")
    time.sleep(0.20)
    if not input_text(text):
        return _delivery_failed_safe(metadata, "text_input_failed")
    metadata["text_entered"] = True

    if require_latest_click_binding:
        latest = get_latest_private_click_event(self_uid=self_uid)
        if not event_matches_target(latest, target_uid, self_uid=self_uid):
            return _delivery_failed_safe(metadata, "conversation_changed_before_send_tap")

    if not send_message():
        return _delivery_failed_safe(metadata, "send_tap_failed")
    metadata.update({
        "send_tap_attempted": True,
        "delivery_state": DeliveryState.ATTEMPTED.value,
        "reason": "send_tapped_waiting_for_server_echo",
    })

    if not require_server_echo:
        metadata.update({
            "confirmed": False,
            "delivery_state": DeliveryState.AMBIGUOUS.value,
            "reason": "server_echo_requirement_disabled",
        })
        return metadata

    try:
        echo = wait_for_own_chat_echo(
            baseline_lines=send_baseline,
            room_id=room_id,
            self_uid=self_uid,
            text=text,
        )
    except Exception as exc:
        # We already observed a successful send tap. An ADB/logcat failure
        # afterwards cannot be retried automatically without risking a dupe.
        metadata.update({
            "confirmed": False,
            "delivery_state": DeliveryState.AMBIGUOUS.value,
            "reason": f"server_echo_observation_error:{type(exc).__name__}",
        })
        return metadata
    if echo is None:
        print("[SEND UNCONFIRMED] Send was tapped, but no matching own push.chat.room echo appeared.")
        metadata.update({
            "confirmed": False,
            "delivery_state": DeliveryState.AMBIGUOUS.value,
            "reason": "server_echo_not_seen_after_send_tap",
        })
        return metadata

    metadata.update({
        "confirmed": True,
        "confirmation_source": "server_echo",
        "server_echo_seen": True,
        "server_echo_seq": echo.seq_id,
        "seq_id": echo.seq_id,
        "server_time": echo.server_time,
        "delivery_state": DeliveryState.CONFIRMED.value,
        "reason": "server_echo_confirmed",
    })
    return metadata


def _delivery_failed_safe(metadata: dict[str, Any], reason: str) -> dict[str, Any]:
    metadata.update({
        "confirmed": False,
        "delivery_state": DeliveryState.FAILED_SAFE.value,
        "reason": str(reason),
    })
    print("[SEND BLOCKED]", reason)
    return metadata


# ======================================================================
# SELF TEST - NO BLUESTACKS REQUIRED
# ======================================================================


def _self_test() -> None:
    test_peer_uid = "9000000000000002"

    sample_click = (
        "10-14 01:20:10.123  1234  5678 I Unity   : "
        "[LUA]OnClickRoomItemPRIVATE_"
        f"{test_peer_uid}_to_{SELF_UID}"
    )

    event = parse_private_click_event(
        sample_click
    )

    assert event is not None
    assert (
        event.target_uid
        == test_peer_uid
    )
    assert (
        event.self_uid
        == SELF_UID
    )

    room_id = (
        f"custom_{test_peer_uid}_{SELF_UID}_v2"
    )

    assert validate_private_room(
        room_id,
        test_peer_uid,
    )

    assert not validate_private_room(
        room_id,
        "999999999",
    )

    sample_payload = {
        "data": {
            "seqId": 300,
            "sender": SELF_UID,
            "roomId": room_id,
            "msg": "hello from PEva",
            "serverTime": 1791930738885,
            "group": "custom",
        },
        "cmd": "push.chat.up.room",
    }

    sample_json_line = (
        "10-14 01:20:11.000 1234 5678 I Unity : "
        "[LUA][callback] "
        + json.dumps(
            sample_payload,
            separators=(",", ":"),
        )
    )

    parsed = _parse_callback_json(
        sample_json_line
    )

    assert parsed is not None
    assert (
        parsed["cmd"]
        == "push.chat.up.room"
    )

    echo = _find_matching_own_echo(
        [sample_json_line],
        baseline_lines=set(),
        room_id=room_id,
        self_uid=SELF_UID,
        text="hello from PEva",
    )

    assert echo is not None
    assert echo.seq_id == 300

    legacy_payload = dict(sample_payload)
    legacy_payload["cmd"] = "push.chat.room"
    legacy_line = (
        "10-14 01:20:11.000 1234 5678 I Unity : "
        "[LUA][callback] "
        + json.dumps(legacy_payload, separators=(",", ":"))
    )
    assert _find_matching_own_echo(
        [legacy_line],
        baseline_lines=set(),
        room_id=room_id,
        self_uid=SELF_UID,
        text="hello from PEva",
    ) is not None

    print(
        "Click parser target:",
        event.target_uid,
    )
    print(
        "Room validation: OK"
    )
    print(
        "Own chat echo parser: OK"
    )
    print()
    print(
        "SELF-TEST OK"
    )


# ======================================================================
# LIVE UID FINDER - DOES NOT SEND TEXT
# ======================================================================


def _live_find_private(
    target_uid: str,
) -> None:
    if not check_connection():
        raise SystemExit(
            "BlueStacks is not connected."
        )

    print(
        "Open Last Z -> chat -> PRIVATE list first."
    )
    print(
        "No text will be sent."
    )
    print(
        "Searching visible PM rows for UID:",
        target_uid,
    )
    print()

    result = find_private_conversation_visible(
        target_uid
    )

    if not result.found:
        print(
            "[NOT FOUND]",
            result.reason,
        )
        return

    print(
        "[FOUND]",
        f"UID={target_uid}",
        f"index={result.index}",
    )
    print(
        "The matching private conversation "
        "was left open."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Last Z ADB control + UID-verified PM navigation."
        )
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "Test parsers without BlueStacks."
        ),
    )

    parser.add_argument(
        "--find-private",
        metavar="UID",
        help=(
            "From the already-open PRIVATE chat list, "
            "find a visible player by UID. Does not send text."
        ),
    )

    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

    if args.find_private:
        _live_find_private(
            args.find_private
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
