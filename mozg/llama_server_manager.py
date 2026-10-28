"""Optional, ownership-aware lifecycle management for llama-server."""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .lastz_ai import AIConfig, LastZAI
from config.runtime_settings import PevaSettings, ResolvedModel, SettingsError


@dataclass(slots=True)
class LlamaServerManager:
    settings: PevaSettings
    model: ResolvedModel
    health_check: Callable[[], bool] | None = None
    process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    process: subprocess.Popen[bytes] | None = None
    managed: bool = False

    def ensure_running(self, *, timeout_seconds: float = 25.0) -> bool:
        """Reuse a healthy server or start and own exactly one process."""
        if self._healthy():
            self.managed = False
            return False
        if _port_is_open(self.settings.llm.host, self.settings.llm.port):
            raise SettingsError(
                f"LLM port {self.settings.llm.host}:{self.settings.llm.port} is occupied, "
                "but no healthy compatible llama-server responded."
            )
        executable = self.settings.llama_server.executable
        if executable is None or not executable.is_file():
            raise SettingsError("llama_server.executable must name an existing llama-server executable.")
        if not self.model.path.is_file():
            raise SettingsError(f"Selected chat model file does not exist: {self.model.path}")
        command = self.command(executable)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = self.process_factory(
                command,
                cwd=str(self.settings.project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise SettingsError(f"Could not start llama-server {executable}: {exc}") from exc
        self.managed = True
        deadline = self.monotonic() + max(1.0, float(timeout_seconds))
        while self.monotonic() < deadline:
            if self._healthy():
                return True
            if self.process.poll() is not None:
                code = self.process.returncode
                self.managed = False
                raise SettingsError(f"llama-server exited before becoming healthy (exit code {code}).")
            self.sleep(0.20)
        self.close(force=True)
        raise SettingsError(
            f"llama-server did not become healthy at {self.settings.llm.endpoint} within {timeout_seconds:.0f}s."
        )

    def command(self, executable: Path | None = None) -> list[str]:
        executable = executable or self.settings.llama_server.executable
        if executable is None:
            raise SettingsError("llama_server.executable is not configured.")
        server = self.settings.llama_server
        command = [
            str(executable),
            "--model", str(self.model.path),
            "--host", self.settings.llm.host,
            "--port", str(self.settings.llm.port),
            "--n-gpu-layers", str(server.gpu_layers),
            "--ctx-size", str(server.context_size),
        ]
        if server.threads:
            command.extend(["--threads", str(server.threads)])
        return command

    def close(self, *, force: bool = False) -> None:
        """Terminate only a process launched by this manager."""
        if not self.managed or self.process is None:
            return
        if not force and not self.settings.llm.shutdown_managed_server:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5.0)
        self.managed = False

    def _healthy(self) -> bool:
        if self.health_check is not None:
            try:
                return bool(self.health_check())
            except Exception:
                return False
        config = AIConfig(base_url=self.settings.llm.endpoint)
        return LastZAI(config=config).health_check()


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def start_configured_server(
    settings: PevaSettings,
    model: ResolvedModel,
) -> LlamaServerManager | None:
    """Start only when explicitly enabled; otherwise preserve external-server mode."""
    if not settings.llm.auto_start_server:
        return None
    manager = LlamaServerManager(settings, model)
    manager.ensure_running()
    return manager
