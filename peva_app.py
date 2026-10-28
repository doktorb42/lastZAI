"""Zwykłe okno startowe nowej, bezpiecznej PEvy.

Jedno kliknięcie uruchamia jej Model, Oczy, Mózg, Charakter i Emocje.  SAFE
pozostaje włączony od startu.  Osobny, wyraźny przycisk może potem włączyć
odpowiedzi wyłącznie dla zatwierdzonych osób.  Ciężkie ładowanie modelu odbywa
się poza wątkiem okna, aby przycisk STOP zawsze pozostał dostępny.
"""

from __future__ import annotations

import queue
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk

from peva_panel import LocalPanel
from peva_runtime import PEvaRuntime


class PEvaWindow:
    """Małe, lokalne sterowanie jednym runtime PEva."""

    def __init__(self, root: tk.Tk, *, runtime: PEvaRuntime | None = None) -> None:
        self.root = root
        self.runtime = runtime or PEvaRuntime()
        self.panel = LocalPanel(self.runtime)
        self._closing = threading.Event()
        self._events: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._start_thread: threading.Thread | None = None
        self.summary = tk.StringVar(value="PEva jest wyłączona.")
        self.detail = tk.StringVar(value="Kliknij „Uruchom PEvę”, aby załadować model i włączyć Oczy.")
        root.title("PEva")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        root.after(250, self._refresh)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="PEva", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            frame,
            text="Model i Oczy uruchamiają się razem. SAFE jest włączony od startu.",
            justify="left",
        ).grid(row=1, column=0, columnspan=3, pady=(5, 10), sticky="w")
        ttk.Label(frame, textvariable=self.summary, font=("Segoe UI", 10, "bold")).grid(
            row=2, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(frame, textvariable=self.detail, wraplength=460, justify="left").grid(
            row=3, column=0, columnspan=3, pady=(6, 14), sticky="w"
        )
        self.start_button = ttk.Button(frame, text="Uruchom PEvę", command=self.start)
        self.start_button.grid(row=4, column=0, padx=(0, 7), sticky="ew")
        self.safe_button = ttk.Button(frame, text="SAFE", command=self.stop_actions, state="disabled")
        self.safe_button.grid(row=4, column=1, padx=(0, 7), sticky="ew")
        self.panel_button = ttk.Button(frame, text="Otwórz panel", command=self.open_panel, state="disabled")
        self.panel_button.grid(row=4, column=2, sticky="ew")
        self.reply_button = ttk.Button(
            frame,
            text="Włącz odpowiedzi zatwierdzonym osobom",
            command=self.enable_approved_replies,
            state="disabled",
        )
        self.reply_button.grid(row=5, column=0, columnspan=3, pady=(8, 0), sticky="ew")
        self.stop_button = ttk.Button(frame, text="Wyłącz PEvę", command=self.close, state="disabled")
        self.stop_button.grid(row=6, column=0, columnspan=3, pady=(8, 0), sticky="ew")
        ttk.Label(
            frame,
            text="Automatyczna odpowiedź działa tylko do osoby, której ręcznie zatwierdzisz alias w panelu. SAFE zatrzymuje ją natychmiast.",
            foreground="#555555",
            wraplength=460,
            justify="left",
        ).grid(row=7, column=0, columnspan=3, pady=(14, 0), sticky="w")

    def start(self) -> None:
        if self._start_thread is not None and self._start_thread.is_alive():
            return
        self.start_button.configure(state="disabled")
        self.detail.set("Ładuję lokalny model i uruchamiam oszczędne czuwanie Oczu…")
        self._start_thread = threading.Thread(target=self._start_worker, name="PEvaStart", daemon=True)
        self._start_thread.start()

    def _start_worker(self) -> None:
        try:
            self.runtime.start(load_model=True, start_eyes=True)
            if self._closing.is_set():
                self.runtime.close()
                return
            url = self.panel.start()
            self._events.put(("started", f"PEva działa. Panel lokalny: {url}"))
        except Exception as error:
            self._events.put(("error", f"Nie udało się uruchomić PEvy: {type(error).__name__}: {error}"))

    def stop_actions(self) -> None:
        self.runtime.stop_actions("Stopped from PEva window")
        self.detail.set("SAFE jest aktywny. Oczy mogą nadal zbierać informacje, ale Ręce są zablokowane.")

    def enable_approved_replies(self) -> None:
        state = self.runtime.resume_actions("Enabled from PEva window")
        contacts = self.runtime.status_snapshot()["automatic_delivery"]["approved_contacts"]
        if contacts:
            self.detail.set(f"Automatyczne odpowiedzi włączone dla {contacts} zatwierdzonych osób. SAFE zatrzymuje je od razu.")
        else:
            self.detail.set("Brak zatwierdzonej osoby. W panelu najpierw zatwierdź automatyczne odpowiedzi przy jej aliasie.")

    def open_panel(self) -> None:
        url = self.panel.url
        if url:
            webbrowser.open(url)

    def _refresh(self) -> None:
        try:
            while True:
                kind, message = self._events.get_nowait()
                if kind == "started":
                    self.safe_button.configure(state="normal")
                    self.reply_button.configure(state="normal")
                    self.panel_button.configure(state="normal")
                    self.stop_button.configure(state="normal")
                    self.detail.set(message)
                elif kind == "error":
                    self.start_button.configure(state="normal")
                    self.detail.set(message)
        except queue.Empty:
            pass
        if self.runtime.is_running:
            state = self.runtime.status_snapshot()
            model = state["model"]
            model_text = "model gotowy" if model["ready"] else "model niedostępny"
            eyes_text = "Oczy czuwają" if state["eyes_running"] else "Oczy zatrzymane"
            delivery = state["automatic_delivery"]
            delivery_text = "odpowiedzi: włączone" if delivery["enabled"] else "odpowiedzi: zatrzymane"
            self.summary.set(f"{model_text}; {eyes_text}; {delivery_text}; SAFE: {'TAK' if state['safe'] else 'NIE'}")
        if not self._closing.is_set():
            self.root.after(250, self._refresh)

    def close(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        self.detail.set("Zatrzymuję PEvę…")
        self.panel.close()
        self.runtime.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    PEvaWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
