"""Jedyny punkt kontaktu PEva z lokalnym ``llama-server``.

Moduł nie zna ADB ani ekranu gry.  Nie uruchamia modelu przy imporcie;
uruchomienie następuje wyłącznie, gdy centralny runtime PEva zostanie
świadomie wystartowany.  Dzięki temu testy i panel stanu nie pobierają VRAM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .lastz_ai import AIConfig, AIReply, LastZAI
from .llama_server_manager import LlamaServerManager
from config.runtime_settings import (
    ModelRegistry,
    PevaSettings,
    ResolvedModel,
    SettingsError,
    load_model_registry,
    load_settings,
    resolve_model_for_role,
)


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """Krótki status do panelu; nie zawiera promptów ani treści czatu."""

    configured: bool
    ready: bool
    model_name: str | None
    model_path: str | None
    detail: str
    managed_by_peva: bool = False


class LocalModel:
    """Bezpieczny klient jednego lokalnego modelu rozmów.

    ``start`` najpierw wykorzystuje już działający zgodny serwer.  Jeśli go
    nie ma, uruchamia wyłącznie skonfigurowany lokalny ``llama-server`` z
    jednym wskazanym plikiem GGUF.  ``close`` zatrzymuje tylko proces, który
    PEva sama uruchomiła.
    """

    def __init__(
        self,
        *,
        settings: PevaSettings | None = None,
        registry: ModelRegistry | None = None,
        ai_factory: Callable[..., LastZAI] = LastZAI,
        manager_factory: Callable[[PevaSettings, ResolvedModel], LlamaServerManager] = LlamaServerManager,
    ) -> None:
        self.settings = settings or load_settings()
        self.registry = registry or load_model_registry(project_root=self.settings.project_root)
        self._ai_factory = ai_factory
        self._manager_factory = manager_factory
        self._resolved: ResolvedModel | None = None
        self._manager: LlamaServerManager | None = None
        self._ai: LastZAI | None = None
        self._status = ModelStatus(False, False, None, None, "Model has not been prepared yet.")

    @property
    def status(self) -> ModelStatus:
        return self._status

    @property
    def ai(self) -> LastZAI | None:
        return self._ai

    def preflight(self) -> ModelStatus:
        """Sprawdź ustawienia i plik modelu bez uruchamiania GPU."""
        try:
            resolved = resolve_model_for_role(self.settings, self.registry, role="chat")
        except SettingsError as error:
            self._status = ModelStatus(False, False, None, None, str(error))
            return self._status
        self._resolved = resolved
        self._status = ModelStatus(
            True,
            False,
            resolved.name,
            str(resolved.path),
            "Model is configured but not loaded.",
        )
        return self._status

    def start(self) -> ModelStatus:
        """Załaduj model dla świadomie uruchomionej PEva.

        To jest jedyne miejsce, które może uruchomić proces modelu.  Błąd
        modelu nie odbiera Oczu możliwości dalszego czuwania; status zostaje
        widoczny w panelu, a Mózg nie wygeneruje wtedy odpowiedzi.
        """
        # A second Start click must not replace the manager that owns an
        # already-running llama-server.  Replacing it would make ``close``
        # lose ownership and could leave the GPU process behind.
        if self._status.ready:
            return self._status

        prepared = self.preflight()
        if not prepared.configured or self._resolved is None:
            return prepared
        manager: LlamaServerManager | None = None
        try:
            manager = self._manager_factory(self.settings, self._resolved)
            manager.ensure_running()
            config = AIConfig.from_env()
            config.base_url = self.settings.llm.endpoint
            config.model = str(self._resolved.path)
            ai = self._ai_factory(config=config)
            if not ai.health_check():
                raise SettingsError("Local model server did not pass its health check.")
        except Exception as error:
            # If the process did start but a subsequent client check failed,
            # stop only that owned process before reporting the failure.
            if manager is not None:
                try:
                    manager.close(force=True)
                except Exception:
                    pass
            self._status = ModelStatus(
                True,
                False,
                self._resolved.name,
                str(self._resolved.path),
                str(error),
            )
            return self._status
        self._manager = manager
        self._ai = ai
        self._status = ModelStatus(
            True,
            True,
            self._resolved.name,
            str(self._resolved.path),
            "Local model is ready.",
            managed_by_peva=manager.managed,
        )
        return self._status

    def generate_reply_safe(self, incoming_text: str, **kwargs: object) -> AIReply:
        """Wygeneruj tekst wyłącznie, gdy lokalny model jest już gotowy."""
        if self._ai is None or not self._status.ready:
            return AIReply(text="", error="Local model is not ready.")
        return self._ai.generate_reply_safe(incoming_text, **kwargs)

    def close(self) -> None:
        """Zamknij tylko serwer będący własnością PEva."""
        manager = self._manager
        self._manager = None
        self._ai = None
        if manager is not None:
            try:
                manager.close()
            finally:
                pass
        if self._resolved is not None:
            self._status = ModelStatus(
                True,
                False,
                self._resolved.name,
                str(self._resolved.path),
                "Local model was stopped.",
            )

