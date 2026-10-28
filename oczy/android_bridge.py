"""Local transport for exact Last Z text copied by PEva Helper.

The Android companion is intentionally not a permanent clipboard monitor. This
client arms one short session through ADB, then reads a private, short-lived
queue through a provider that admits only the local Android ADB shell. It does not navigate Last Z, tap Send, or infer a
conversation identity; those decisions stay in the safety-checked runtime.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from config.runtime_config import ADB_DEVICE, ADB_PATH


PACKAGE = "com.peva.copybridge"
LOCAL_URI = "content://com.peva.copybridge.local"
ACTION_ARM = "com.peva.copybridge.ARM"
ACTION_STOP = "com.peva.copybridge.STOP"
COMMAND_RECEIVER = "com.peva.copybridge/.BridgeCommandReceiver"


class CopyBridgeError(RuntimeError):
    """The local Android bridge could not be contacted safely."""


@dataclass(frozen=True, slots=True)
class CopyBridgeStatus:
    """Non-content state exposed by the local Android companion."""

    status: str
    armed_until_ms: int
    captured_in_window: int
    pending: int

    @property
    def armed(self) -> bool:
        return self.armed_until_ms > int(time.time() * 1000) and self.status.startswith("Gotowy przez")


@dataclass(frozen=True, slots=True)
class CopiedText:
    """One exact Last Z message from an explicitly armed Copy action."""

    message_id: str
    text: str
    sha256: str
    captured_at_ms: int


@dataclass(slots=True)
class MessageBurst:
    """Collects quick consecutive copies before the model receives them."""

    idle_seconds: float = 1.0
    maximum_seconds: float = 3.5
    _messages: list[CopiedText] = field(default_factory=list, init=False, repr=False)
    _ids: set[str] = field(default_factory=set, init=False, repr=False)
    _first_seen: float | None = field(default=None, init=False, repr=False)
    _last_seen: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.idle_seconds <= 0 or self.maximum_seconds < self.idle_seconds:
            raise ValueError("invalid burst timing")

    @property
    def messages(self) -> tuple[CopiedText, ...]:
        return tuple(self._messages)

    def add(self, messages: Iterable[CopiedText], *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        added = 0
        for message in messages:
            if message.message_id in self._ids:
                continue
            self._ids.add(message.message_id)
            self._messages.append(message)
            if self._first_seen is None:
                self._first_seen = current
            self._last_seen = current
            added += 1
        return added

    def ready(self, *, now: float | None = None) -> bool:
        if self._first_seen is None or self._last_seen is None:
            return False
        current = time.monotonic() if now is None else now
        return (
            current - self._last_seen >= self.idle_seconds
            or current - self._first_seen >= self.maximum_seconds
        )

    def take(self) -> tuple[CopiedText, ...]:
        result = tuple(self._messages)
        self._messages.clear()
        self._ids.clear()
        self._first_seen = None
        self._last_seen = None
        return result


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[bytes]]


class CopyBridge:
    """ADB client for the minimal Android copy companion."""

    def __init__(
        self,
        *,
        adb_path: str = ADB_PATH,
        device: str = ADB_DEVICE,
        runner: Runner | None = None,
    ) -> None:
        self.adb_path = str(adb_path)
        self.device = str(device)
        self._runner = runner or self._run_subprocess

    def arm(self, *, window_ms: int = 8000, max_copies: int = 8) -> None:
        """Arm a bounded clipboard session and wait until its listener is ready.

        ADB broadcasts return before Android necessarily starts the foreground
        service.  A Copy action in that small race window would be lost, so the
        caller is not allowed to touch Last Z until the provider confirms the
        listener is actually armed.
        """
        if not 1000 <= int(window_ms) <= 20000:
            raise ValueError("window_ms must be between 1000 and 20000")
        if not 1 <= int(max_copies) <= 12:
            raise ValueError("max_copies must be between 1 and 12")
        token = self._read_token()
        self._checked(
            "shell", "am", "broadcast",
            "-n", COMMAND_RECEIVER,
            "-a", ACTION_ARM,
            "--es", "token", token,
            "--ei", "window_ms", str(int(window_ms)),
            "--ei", "max_copies", str(int(max_copies)),
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self.status().armed:
                return
            time.sleep(0.05)
        self.stop()
        raise CopyBridgeError("Copy Bridge did not become ready before the capture window")

    def stop(self) -> None:
        """Disarm immediately; the companion also stops itself on timeout."""
        token = self._read_token()
        self._checked(
            "shell", "am", "broadcast",
            "-n", COMMAND_RECEIVER,
            "-a", ACTION_STOP,
            "--es", "token", token,
        )

    def fetch(self) -> tuple[CopiedText, ...]:
        """Read and consume valid records from the companion's private queue."""
        completed = self._checked("shell", "content", "query", "--uri", f"{LOCAL_URI}/inbox")
        raw = bytes(completed.stdout or b"").decode("utf-8", errors="strict")
        return _parse_content_rows(raw)

    def status(self) -> CopyBridgeStatus:
        """Read status only; unlike :meth:`fetch`, this contains no chat text."""
        completed = self._checked("shell", "content", "query", "--uri", f"{LOCAL_URI}/status")
        raw = bytes(completed.stdout or b"").decode("utf-8", errors="strict")
        return _parse_status_row(raw)

    def _read_token(self) -> str:
        completed = self._checked("shell", "content", "query", "--uri", f"{LOCAL_URI}/pair")
        raw = bytes(completed.stdout or b"").decode("ascii", errors="strict")
        match = re.search(r"(?:^|[\s,])token=([^,\s]+)", raw)
        token = match.group(1) if match is not None else ""
        if len(token) < 24 or any(character.isspace() for character in token):
            raise CopyBridgeError("Copy Bridge pairing secret is missing or invalid")
        return token

    def _checked(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        command = (self.adb_path, "-s", self.device, *args)
        try:
            completed = self._runner(command, 10.0)
        except (OSError, subprocess.SubprocessError) as error:
            raise CopyBridgeError("BlueStacks ADB is not available") from error
        if completed.returncode != 0:
            detail = bytes(completed.stderr or b"").decode("utf-8", errors="replace").strip()
            raise CopyBridgeError(detail or "Copy Bridge command failed")
        return completed

    @staticmethod
    def _run_subprocess(
        command: Sequence[str], timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            list(command),
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
        )


def _parse_record(value: object) -> CopiedText | None:
    if not isinstance(value, dict):
        return None
    message_id = value.get("id")
    text = value.get("text")
    sha256 = value.get("sha256")
    captured_at = value.get("captured_at")
    if (
        not isinstance(message_id, str)
        or not message_id
        or not isinstance(text, str)
        or not text
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or not isinstance(captured_at, int)
        or captured_at <= 0
    ):
        return None
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != sha256:
        return None
    return CopiedText(message_id, text, sha256, captured_at)


def _parse_content_rows(raw: str) -> tuple[CopiedText, ...]:
    """Parse Android's `content query` text without exposing message syntax."""
    messages: list[CopiedText] = []
    seen_ids: set[str] = set()
    pattern = re.compile(
        r"Row:\s*\d+\s+_id=([^,\s]+),\s*text_b64=([^,\s]+),\s*"
        r"sha256=([0-9a-f]{64}),\s*captured_at=(\d+)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(raw):
        message_id, encoded_text, fingerprint, captured_at = match.groups()
        try:
            padded = encoded_text + "=" * (-len(encoded_text) % 4)
            text = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            timestamp = int(captured_at)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        message = _parse_record({
            "id": message_id,
            "text": text,
            "sha256": fingerprint.lower(),
            "captured_at": timestamp,
        })
        if message is not None and message.message_id not in seen_ids:
            messages.append(message)
            seen_ids.add(message.message_id)
    return tuple(messages)


def _parse_status_row(raw: str) -> CopyBridgeStatus:
    """Decode the companion's metadata-only Android content row."""
    pattern = re.compile(
        r"status_b64=([^,\s]+),\s*last_length=\d+,\s*captured_at=\d+,\s*"
        r"captured_in_window=(\d+),\s*armed_until=(\d+),\s*pending=(\d+)",
        flags=re.IGNORECASE,
    )
    match = pattern.search(raw)
    if match is None:
        raise CopyBridgeError("Copy Bridge status response was invalid")
    encoded_status, captured, armed_until, pending = match.groups()
    try:
        padded = encoded_status + "=" * (-len(encoded_status) % 4)
        status = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        return CopyBridgeStatus(status, int(armed_until), int(captured), int(pending))
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise CopyBridgeError("Copy Bridge status response was invalid") from error
