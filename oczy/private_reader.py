"""Read-only coordinator for Last Z private-chat visual signals and local OCR.

The coordinator has one tightly-scoped navigation ability: it can open exactly
one visually marked PM row. It has no swipe, text-entry, or send methods. A
short persistent state keeps just a SHA-256 fingerprint, never a chat
transcript, so restarting PEva does not re-read the same screen as a new event.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .vision import UnreadRow
from .private_chat import PrivateChatEyes, PrivateChatObservation
from config.runtime_config import DATA_DIR
from .local_ocr import ScreenOcrBridge, ScreenOcrResult
from .identity import is_blocking_overlay_title
from config.runtime_config import SCREEN_HEIGHT, SCREEN_WIDTH


DEFAULT_STATE_PATH = DATA_DIR / "runtime" / "private_chat_eyes_state.json"
DEFAULT_LIST_REFRESH_IDLE_SECONDS = 45.0
DEFAULT_LIST_REFRESH_COOLDOWN_SECONDS = 90.0


class Eyes(Protocol):
    def observe_once(self) -> PrivateChatObservation: ...


class Ocr(Protocol):
    def capture_visible_screen(self) -> None: ...

    def wait_for_result(self, *, timeout_seconds: float = 8.0) -> ScreenOcrResult: ...


class RowOpener(Protocol):
    def open_unread_row(self, row: UnreadRow) -> bool: ...


class PrivateChatListRefresher(Protocol):
    """A single, narrow tab refresh used only from an already-open PM list."""

    def refresh_private_chat_list(self) -> bool: ...


class ADBUnreadRowOpener:
    """Tap the content area of a detected PM row, never its red-dot edge."""

    def open_unread_row(self, row: UnreadRow) -> bool:
        # Keep the decision and visual proof in Oczy; the only physical
        # gesture itself belongs to Ręce and has no text-entry capability.
        from rece import open_verified_private_chat_row

        return open_verified_private_chat_row(
            row_center_x=row.center_x,
            row_center_y=row.center_y,
        )


class ADBPrivateChatListRefresher:
    """Refresh unread row badges without opening any conversation."""

    def refresh_private_chat_list(self) -> bool:
        from rece import refresh_verified_private_chat_list

        return refresh_verified_private_chat_list(
            screen_width=SCREEN_WIDTH,
            screen_height=SCREEN_HEIGHT,
        )


@dataclass(frozen=True, slots=True)
class PrivateChatReadEvent:
    """Outcome of one explicitly scheduled, read-only observation."""

    kind: str
    observation: PrivateChatObservation
    result: ScreenOcrResult | None = None
    detail: str = ""

    @property
    def has_new_text(self) -> bool:
        return self.kind == "new_text"

    @property
    def needs_conversation_open(self) -> bool:
        return self.kind == "unread_chat_needs_open"


class PrivateChatOcrCoordinator:
    """De-duplicate visual PM events before asking PEva for one OCR read."""

    def __init__(
        self,
        *,
        eyes: Eyes | None = None,
        ocr: Ocr | None = None,
        row_opener: RowOpener | None = None,
        list_refresher: PrivateChatListRefresher | None = None,
        state_path: Path = DEFAULT_STATE_PATH,
        list_refresh_idle_seconds: float = DEFAULT_LIST_REFRESH_IDLE_SECONDS,
        list_refresh_cooldown_seconds: float = DEFAULT_LIST_REFRESH_COOLDOWN_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not 15.0 <= float(list_refresh_idle_seconds) <= 600.0:
            raise ValueError("list_refresh_idle_seconds must be between 15 and 600")
        if not 30.0 <= float(list_refresh_cooldown_seconds) <= 1_800.0:
            raise ValueError("list_refresh_cooldown_seconds must be between 30 and 1800")
        self.eyes = eyes or PrivateChatEyes()
        self.ocr = ocr or ScreenOcrBridge()
        self.row_opener = row_opener or ADBUnreadRowOpener()
        self.list_refresher = list_refresher or ADBPrivateChatListRefresher()
        self.state_path = Path(state_path)
        self._clock = clock or time.monotonic
        self.list_refresh_idle_seconds = float(list_refresh_idle_seconds)
        self.list_refresh_cooldown_seconds = float(list_refresh_cooldown_seconds)
        self._last_visual_key, self._last_text_sha256 = self._load_fingerprints()
        # A visible red dot can remain on screen briefly after a tap.  Keep a
        # session-only fingerprint of the attempted row so a watcher never
        # taps the same row again and again when the game is slow to change.
        self._last_open_attempt_key: str | None = None
        self._quiet_list_since: float | None = None
        self._last_list_refresh_at: float | None = None

    def scan_once(self) -> PrivateChatReadEvent:
        """Observe once and, only when justified, run one local OCR request."""
        observation = self.eyes.observe_once()
        now = self._clock()

        if observation.is_private_conversation and observation.incoming_bubbles:
            self._quiet_list_since = None
            visual_key = _bubble_key(observation)
            if visual_key == self._last_visual_key:
                return PrivateChatReadEvent(
                    "already_seen_visual",
                    observation,
                    detail="Widoczne bąbelki nie zmieniły się od poprzedniego odczytu.",
                )
            self._last_visual_key = visual_key
            return self._read_visible_conversation(observation)

        # The global top Private Chat badge is ambiguous: Group activity
        # lights it too. A lower-left Private Chat badge is merely a signal to
        # repaint the list through Friend Post -> global Private Chat. It is
        # not a target to tap itself.
        if (
            observation.private_tab_badge
            and observation.screen == "PRIVATE_CHAT"
            and not observation.unread_rows
        ):
            refresh = self._refresh_quiet_private_list(
                observation,
                now=now,
                immediate=True,
            )
            if refresh is not None:
                return refresh

        # A tab/list red dot proves that a private message exists, but it does
        # not identify a conversation safely.  Navigation is deliberately a
        # separate, future read-only stage.
        if observation.should_prepare_copy:
            self._quiet_list_since = None
            return PrivateChatReadEvent(
                "unread_chat_needs_open",
                observation,
                detail="Nowa wiadomość jest w liście prywatnych czatów; rozmowa nie została otwarta.",
            )

        if observation.screen == "PRIVATE_CHAT":
            refresh = self._refresh_quiet_private_list(observation, now=now)
            if refresh is not None:
                return refresh
        else:
            self._quiet_list_since = None
        self._last_visual_key = None
        self._last_open_attempt_key = None
        return PrivateChatReadEvent("idle", observation)

    def _refresh_quiet_private_list(
        self,
        observation: PrivateChatObservation,
        *,
        now: float,
        immediate: bool = False,
    ) -> PrivateChatReadEvent | None:
        """Request one bounded repaint only after a stable, empty PM list.

        This deliberately does *not* navigate away from an open conversation
        or from another Last Z screen.  Standard Android/ADB access cannot
        reliably observe the user's taps, so "inactive" is defined here as an
        unchanged, already-visible private-chat list with no unread signal.
        """
        if self._quiet_list_since is None and not immediate:
            self._quiet_list_since = now
            return None
        idle_for = (
            self.list_refresh_idle_seconds
            if immediate
            else now - (self._quiet_list_since or now)
        )
        since_refresh = (
            float("inf")
            if self._last_list_refresh_at is None
            else now - self._last_list_refresh_at
        )
        if (
            (not immediate and idle_for < self.list_refresh_idle_seconds)
            or since_refresh < self.list_refresh_cooldown_seconds
        ):
            return None
        self._last_list_refresh_at = now
        self._quiet_list_since = now
        if self.list_refresher.refresh_private_chat_list():
            return PrivateChatReadEvent(
                "private_chat_list_refresh_started",
                observation,
                detail="Odświeżam listę Prywatny czat, aby Last Z ponownie narysował kropki.",
            )
        return PrivateChatReadEvent(
            "private_chat_list_refresh_failed",
            observation,
            detail="Nie udało się bezpiecznie odświeżyć zakładki Prywatny czat.",
        )

    def open_single_unread_and_read(
        self,
        *,
        open_timeout_seconds: float = 2.0,
    ) -> PrivateChatReadEvent:
        """Open exactly one marked row, re-observe, then read it once.

        This fails closed when more than one row is marked, the list is not
        confirmed, a tap fails, or the resulting screen is not a private
        conversation. It never types or sends text.
        """
        if not 0.3 <= float(open_timeout_seconds) <= 5.0:
            raise ValueError("open_timeout_seconds must be between 0.3 and 5")

        listed = self.scan_once()
        observation = listed.observation
        if listed.kind != "unread_chat_needs_open":
            return listed
        if observation.screen != "PRIVATE_CHAT":
            return PrivateChatReadEvent(
                "unread_chat_list_not_visible",
                observation,
                detail="Widać sygnał nowej wiadomości, ale lista prywatnych czatów nie jest otwarta.",
            )
        if len(observation.unread_rows) != 1:
            return PrivateChatReadEvent(
                "ambiguous_unread_rows",
                observation,
                detail="PEva otwiera automatycznie tylko dokładnie jeden oznaczony wiersz.",
            )

        row = observation.unread_rows[0]
        row_key = _unread_row_key(row)
        if row_key == self._last_open_attempt_key:
            return PrivateChatReadEvent(
                "already_seen_unread_row",
                observation,
                detail="Ten sam oznaczony wiersz był już bezpiecznie sprawdzony w tej sesji.",
            )
        self._last_open_attempt_key = row_key
        if not self.row_opener.open_unread_row(row):
            return PrivateChatReadEvent(
                "row_open_failed",
                observation,
                detail="Nie udało się otworzyć oznaczonego wiersza prywatnej rozmowy.",
            )

        deadline = time.monotonic() + float(open_timeout_seconds)
        last_observation = observation
        while time.monotonic() < deadline:
            current = self.eyes.observe_once()
            last_observation = current
            if current.is_private_conversation:
                if not current.incoming_bubbles:
                    return PrivateChatReadEvent(
                        "conversation_open_no_bubbles",
                        current,
                        detail="Rozmowa otworzyła się, ale nie ma widocznych bąbelków przychodzących.",
                    )
                visual_key = _bubble_key(current)
                if visual_key == self._last_visual_key:
                    return PrivateChatReadEvent(
                        "already_seen_visual",
                        current,
                        detail="Widoczne bąbelki nie zmieniły się od poprzedniego odczytu.",
                    )
                self._last_visual_key = visual_key
                return self._read_visible_conversation(current)
            time.sleep(0.15)

        return PrivateChatReadEvent(
            "conversation_open_not_confirmed",
            last_observation,
            detail="Po otwarciu wiersza nie potwierdzono widoku prywatnej rozmowy.",
        )

    def _read_visible_conversation(
        self,
        observation: PrivateChatObservation,
    ) -> PrivateChatReadEvent:
        try:
            self.ocr.capture_visible_screen()
            result = self.ocr.wait_for_result(timeout_seconds=8.0)
        except Exception as error:
            return PrivateChatReadEvent(
                "ocr_error",
                observation,
                detail=f"{type(error).__name__}: {error}",
            )

        # A modal Survivor Profile can cover the PM while leaving the old
        # bubbles visible in the background.  The Android helper extracts the
        # centred modal title in its header crop, so fail closed before text
        # is fingerprinted, persisted, or passed to packet collection.
        if is_blocking_overlay_title(result.header_text):
            return PrivateChatReadEvent(
                "overlay_open",
                observation,
                result,
                "Okno profilu zasłania rozmowę; PEva pomija ten skan.",
            )

        normalized = "\n".join(
            line.strip() for line in result.text.splitlines() if line.strip()
        )
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if not normalized:
            return PrivateChatReadEvent(
                "empty_text",
                observation,
                result,
                "OCR nie zwrócił czytelnego tekstu.",
            )
        if fingerprint == self._last_text_sha256:
            self._save_fingerprints(
                text_fingerprint=fingerprint,
                visual_fingerprint=self._last_visual_key,
                captured_at_ms=result.captured_at_ms,
            )
            return PrivateChatReadEvent(
                "duplicate_text",
                observation,
                result,
                "Tekst ekranu nie zmienił się od ostatniego odczytu.",
            )

        self._last_text_sha256 = fingerprint
        self._save_fingerprints(
            text_fingerprint=fingerprint,
            visual_fingerprint=self._last_visual_key,
            captured_at_ms=result.captured_at_ms,
        )
        return PrivateChatReadEvent("new_text", observation, result)

    def _load_fingerprints(self) -> tuple[str | None, str | None]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        if not isinstance(payload, dict):
            return None, None
        visual = payload.get("last_visual_sha256")
        text = payload.get("last_text_sha256")
        valid_visual = visual if isinstance(visual, str) and len(visual) == 64 else None
        valid_text = text if isinstance(text, str) and len(text) == 64 else None
        return valid_visual, valid_text

    def _save_fingerprints(
        self,
        *,
        text_fingerprint: str,
        visual_fingerprint: str | None,
        captured_at_ms: int,
    ) -> None:
        payload = {
            "schema_version": 1,
            "last_text_sha256": text_fingerprint,
            "last_visual_sha256": visual_fingerprint or "",
            "captured_at_ms": int(captured_at_ms),
            "saved_at_ms": int(time.time() * 1000),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)


def _bubble_key(observation: PrivateChatObservation) -> str:
    geometry = tuple(
        (bubble.left, bubble.top, bubble.right, bubble.bottom)
        for bubble in observation.incoming_bubbles
    )
    source = f"{observation.screen}|{geometry}".encode("ascii")
    return hashlib.sha256(source).hexdigest()


def _unread_row_key(row: UnreadRow) -> str:
    """Fingerprint a single visible row without storing player data."""
    source = f"{row.center_x}|{row.center_y}".encode("ascii")
    return hashlib.sha256(source).hexdigest()
