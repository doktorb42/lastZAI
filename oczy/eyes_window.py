"""Small desktop window for PEva's manually controlled chat standby mode.

There is no console UI, model startup, text entry, or Send action here.  The
window only starts/stops the existing low-power visual watcher.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from oczy.watch import WATCH_INTERVAL_SECONDS, describe_event
from oczy.packets import PacketBurstBuffer, PrivateChatPacketReader
from oczy.private_reader import PrivateChatOcrCoordinator


WatchFactory = Callable[[], PrivateChatOcrCoordinator]


class EyesWatchWorker:
    """Keep ADB/OCR work off the desktop window's event loop."""

    def __init__(
        self,
        *,
        coordinator_factory: WatchFactory = PrivateChatOcrCoordinator,
        interval_seconds: float = WATCH_INTERVAL_SECONDS,
    ) -> None:
        self._factory = coordinator_factory
        self._interval_seconds = interval_seconds
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._coordinator: PrivateChatOcrCoordinator | None = None
        self.events: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.is_running:
            return False
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, name="PEvaEyes", daemon=True)
        self._thread.start()
        return True

    def request_stop(self) -> None:
        """Stop promptly without making the window wait for ADB."""
        self._stop_requested.set()
        coordinator = self._coordinator
        if coordinator is not None:
            threading.Thread(
                target=_stop_android_helper,
                args=(coordinator,),
                name="PEvaEyesStop",
                daemon=True,
            ).start()

    def _run(self) -> None:
        coordinator: PrivateChatOcrCoordinator | None = None
        last_announced: tuple[str, str] | None = None
        try:
            coordinator = self._factory()
            self._coordinator = coordinator
            reader = PrivateChatPacketReader(coordinator=coordinator)
            burst = PacketBurstBuffer()
            self.events.put(("running", "Czuwanie działa. Ekran jest sprawdzany raz na sekundę."))
            while not self._stop_requested.is_set():
                started = time.monotonic()
                try:
                    event = reader.poll_once()
                    if event.packet is not None:
                        burst.add(event.packet)
                        line = (
                            f"[ZBIERAM] Odczytano fragment rozmowy "
                            f"({event.packet.page_count} ekran/y). Czekam chwilę na kolejne wiadomości."
                        )
                        signature = (event.kind, event.detail)
                    elif burst.pending and burst.ready():
                        packet = burst.take()
                        assert packet is not None
                        nontext = " W paczce jest też element bez pewnego tekstu." if packet.contains_nontext_signal else ""
                        line = (
                            f"[GOTOWE] Zebrano paczkę ({packet.page_count} ekran/y, "
                            f"{len(packet.text)} znaków). PEva niczego nie wysłała.{nontext}"
                        )
                        signature = ("packet_ready", packet.stop_reason)
                    else:
                        line = describe_event(event.source)
                        signature = (event.source.kind, event.source.detail)
                except Exception as error:
                    line = f"[PROBLEM] {type(error).__name__}: {error}"
                    signature = ("watch_error", line)
                if line is not None and signature != last_announced:
                    self.events.put(("notice", line))
                    last_announced = signature
                if line is None:
                    last_announced = None
                remaining = self._interval_seconds - (time.monotonic() - started)
                if remaining > 0:
                    self._stop_requested.wait(remaining)
        finally:
            if coordinator is not None:
                _stop_android_helper(coordinator)
            self._coordinator = None
            self.events.put(("stopped", "Czuwanie jest wyłączone. Androidowy pomocnik został zatrzymany."))


def _stop_android_helper(coordinator: PrivateChatOcrCoordinator) -> None:
    stop = getattr(coordinator.ocr, "stop", None)
    if callable(stop):
        try:
            stop()
        except Exception:
            pass


class PEvaEyesWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PEva — czuwanie wiadomości")
        self.root.resizable(False, False)
        self.worker = EyesWatchWorker()
        self.status = tk.StringVar(value="Czuwanie jest wyłączone.")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._drain_events)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="PEva — czuwanie prywatnego czatu", font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            frame,
            text=(
                "PEva nie uruchamia modelu i nie wysyła wiadomości.\n"
                "Odczyt następuje tylko po nowym sygnale na ekranie."
            ),
            justify="left",
        ).grid(row=1, column=0, columnspan=2, pady=(8, 12), sticky="w")
        ttk.Label(frame, textvariable=self.status, wraplength=420, justify="left").grid(
            row=2, column=0, columnspan=2, pady=(0, 14), sticky="w"
        )
        self.start_button = ttk.Button(frame, text="Włącz czuwanie", command=self._start)
        self.start_button.grid(row=3, column=0, padx=(0, 8), sticky="ew")
        self.stop_button = ttk.Button(frame, text="Zatrzymaj", command=self._stop, state="disabled")
        self.stop_button.grid(row=3, column=1, sticky="ew")
        ttk.Label(
            frame,
            text="Możesz zamknąć to okno w dowolnej chwili.",
            foreground="#555555",
        ).grid(row=4, column=0, columnspan=2, pady=(12, 0), sticky="w")

    def _start(self) -> None:
        if self.worker.start():
            self.status.set("Uruchamiam czuwanie…")
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")

    def _stop(self) -> None:
        self.status.set("Zatrzymuję czuwanie…")
        self.stop_button.configure(state="disabled")
        self.worker.request_stop()

    def _drain_events(self) -> None:
        try:
            while True:
                state, message = self.worker.events.get_nowait()
                self.status.set(message)
                if state == "stopped":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _close(self) -> None:
        self.worker.request_stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    PEvaEyesWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
