from __future__ import annotations

import os
import re
from pathlib import Path


# ======================================================================
# PEVA RUNTIME CONFIGURATION
# ======================================================================
#
# Environment-specific values live here instead of being duplicated across
# chat_capture.py, agent.py, adb_controll.py and screenDetector.py.
#
# Every value can be overridden without editing source code:
#   LASTZ_SELF_UID
#   LASTZ_ADB_PATH
#   LASTZ_ADB_DEVICE
#   LASTZ_SCREEN_WIDTH
#   LASTZ_SCREEN_HEIGHT
#   LASTZ_MAX_MESSAGE_CHARS
#   LASTZ_PROMPTS_DIR
#   LASTZ_TEMPLATES_DIR
#   LASTZ_SCREENSHOT_DIR
#   LASTZ_CONVERSATION_DB
#
# Game/protocol constants (package names, known room format, UI coordinates)
# remain deterministic code constants and are validated separately.

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


SELF_UID = _env_text(
    "LASTZ_SELF_UID",
    "1224463480000772",
)

ADB_PATH = _env_text(
    "LASTZ_ADB_PATH",
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
)

ADB_DEVICE = _env_text(
    "LASTZ_ADB_DEVICE",
    "emulator-5554",
)

SCREEN_WIDTH = _env_int(
    "LASTZ_SCREEN_WIDTH",
    900,
)
SCREEN_HEIGHT = _env_int(
    "LASTZ_SCREEN_HEIGHT",
    1600,
)

MAX_MESSAGE_CHARS = _env_int(
    "LASTZ_MAX_MESSAGE_CHARS",
    950,
)

PROMPTS_DIR = Path(
    _env_text(
        "LASTZ_PROMPTS_DIR",
        str(PROJECT_DIR / "config" / "prompts"),
    )
).expanduser().resolve()

TEMPLATES_DIR = Path(
    _env_text(
        "LASTZ_TEMPLATES_DIR",
        str(PROJECT_DIR / "ui" / "templates"),
    )
).expanduser().resolve()

SCREENSHOT_DIR = Path(
    _env_text(
        "LASTZ_SCREENSHOT_DIR",
        str(DATA_DIR / "screenshots"),
    )
).expanduser().resolve()

CONVERSATION_DB_PATH = Path(
    _env_text(
        "LASTZ_CONVERSATION_DB",
        str(DATA_DIR / "databases" / "conversation.db"),
    )
).expanduser().resolve()


# Direct Last Z PM room observed in the live client:
#   custom_<uidA>_<uidB>_vN
PRIVATE_ROOM_RE = re.compile(
    r"^custom_(\d+)_(\d+)_v(\d+)$"
)


def parse_private_room(
    room_id: str,
) -> tuple[str, str, int] | None:
    if not isinstance(room_id, str):
        return None

    match = PRIVATE_ROOM_RE.fullmatch(
        room_id.strip()
    )

    if match is None:
        return None

    uid_a, uid_b, version = match.groups()

    if uid_a == uid_b:
        return None

    return uid_a, uid_b, int(version)


def is_direct_private_room(
    room_id: str,
    *,
    self_uid: str,
    peer_uid: str,
) -> bool:
    parsed = parse_private_room(room_id)

    if parsed is None:
        return False

    uid_a, uid_b, _version = parsed

    return {
        uid_a,
        uid_b,
    } == {
        str(self_uid).strip(),
        str(peer_uid).strip(),
    }
