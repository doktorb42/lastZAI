"""Mostek zgodności do tylko-odczytowego Capture starego runtime.

Zwykły start PEva nie importuje tego modułu.  Nazwa zostaje wyłącznie dla
zachowanych narzędzi i testów, podczas gdy właściwa implementacja jest
odseparowana w ``archive/legacy_runtime``.
"""

from archive.legacy_runtime.chat_capture import (
    CHAT_PUSH_COMMANDS,
    DEFAULT_ADB_PATH,
    DEFAULT_DEVICE,
    DEFAULT_SELF_UID,
    ChatCapture,
    ChatCaptureConfig,
    ChatMessage,
    main,
)

__all__ = [
    "CHAT_PUSH_COMMANDS",
    "DEFAULT_ADB_PATH",
    "DEFAULT_DEVICE",
    "DEFAULT_SELF_UID",
    "ChatCapture",
    "ChatCaptureConfig",
    "ChatMessage",
    "main",
]


if __name__ == "__main__":
    main()
