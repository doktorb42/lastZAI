"""Manual, foreground-only standby mode for PEva's private-chat eyes.

It samples the visible BlueStacks window once per second.  Android OCR runs
only after the visual detector sees a new incoming chat event.  This module
does not load a language model, type into Last Z, or press Send.
"""

from __future__ import annotations

import time
from typing import Callable

from .private_reader import PrivateChatOcrCoordinator, PrivateChatReadEvent


WATCH_INTERVAL_SECONDS = 1.0


def describe_event(event: PrivateChatReadEvent) -> str | None:
    """Return a privacy-safe status line, without printing chat text."""
    if event.kind == "new_text":
        length = len(event.result.text) if event.result is not None else 0
        return f"[NOWA WIADOMOSC] Lokalny odczyt gotowy ({length} znakow)."
    if event.kind == "duplicate_text":
        return "[BEZ ZMIAN] Ekran ma ten sam tekst co poprzednio."
    if event.kind == "unread_chat_list_not_visible":
        return "[CZEKAM] Jest nowa wiadomosc. Otworz liste Prywatny czat, aby PEva mogla ja odczytac."
    if event.kind == "ambiguous_unread_rows":
        return "[UWAGA] Jest wiecej niz jedna oznaczona rozmowa; PEva niczego nie wybiera."
    if event.kind in {"row_open_failed", "conversation_open_not_confirmed", "ocr_error"}:
        return f"[PROBLEM] {event.detail}"
    if event.kind == "conversation_open_no_bubbles":
        return "[CZEKAM] Rozmowa jest otwarta, ale nie widze wiadomosci przychodzacej."
    if event.kind == "private_chat_list_refresh_started":
        return "[ODSWIEZAM] Sprawdzam ponownie kropki na liscie Prywatny czat."
    if event.kind == "private_chat_list_refresh_failed":
        return f"[PROBLEM] {event.detail}"
    return None


def run_watch(
    *,
    coordinator: PrivateChatOcrCoordinator | None = None,
    interval_seconds: float = WATCH_INTERVAL_SECONDS,
    max_cycles: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
) -> None:
    """Run visibly until Ctrl+C, then explicitly stop the Android helper."""
    if interval_seconds < 0.5:
        raise ValueError("interval_seconds must be at least 0.5")
    active = coordinator or PrivateChatOcrCoordinator()
    output("PEva czuwa. Ctrl+C lub zamkniecie tego okna natychmiast zatrzymuje czuwanie.")
    output("Nie uruchamiam modelu i nie wysylam wiadomosci. Odczyt OCR jest tylko po nowym sygnale.")
    last_announced: tuple[str, str] | None = None
    completed_cycles = 0
    try:
        while max_cycles is None or completed_cycles < max_cycles:
            started = time.monotonic()
            try:
                event = active.open_single_unread_and_read()
            except Exception as error:
                line = f"[PROBLEM] {type(error).__name__}: {error}"
                signature = ("watch_error", line)
            else:
                line = describe_event(event)
                signature = (event.kind, event.detail)
            if line is not None and signature != last_announced:
                output(line)
                last_announced = signature
            if line is None:
                last_announced = None
            completed_cycles += 1
            remaining = interval_seconds - (time.monotonic() - started)
            if remaining > 0 and (max_cycles is None or completed_cycles < max_cycles):
                sleeper(remaining)
    except KeyboardInterrupt:
        output("Zatrzymuje czuwanie PEva...")
    finally:
        stop = getattr(active.ocr, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        output("PEva nie czuwa. Androidowy pomocnik zostal zatrzymany.")


if __name__ == "__main__":
    run_watch()
