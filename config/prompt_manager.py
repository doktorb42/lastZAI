from __future__ import annotations

import os
import re
from pathlib import Path


class PromptError(RuntimeError):
    """Prompt file could not be loaded safely."""


class PromptManager:
    """
    Small prompt loader for PEva.

    Prompts live in:
        ./config/prompts/<name>.txt

    Features:
    - UTF-8 loading
    - path traversal protection
    - lightweight mtime cache
    - automatic reload after editing a .txt file
    """

    _VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

    def __init__(
        self,
        prompts_dir: str | Path | None = None,
        *,
        auto_reload: bool = True,
    ):
        if prompts_dir is None:
            env_dir = os.getenv("LASTZ_PROMPTS_DIR")

            if env_dir:
                prompts_dir = Path(env_dir)
            else:
                prompts_dir = Path(__file__).resolve().parent / "prompts"

        self.prompts_dir = Path(prompts_dir).resolve()
        self.auto_reload = bool(auto_reload)

        # name -> (mtime_ns, text)
        self._cache: dict[str, tuple[int, str]] = {}

    def get(
        self,
        name: str,
    ) -> str:
        """
        Load a prompt by logical name.

        Example:
            prompts.get("reply_system")
        """

        path = self._resolve(name)

        if not path.is_file():
            raise PromptError(
                f"Prompt not found: {path}"
            )

        try:
            stat = path.stat()
        except OSError as exc:
            raise PromptError(
                f"Cannot stat prompt: {path}"
            ) from exc

        cached = self._cache.get(name)

        if cached is not None:
            cached_mtime, cached_text = cached

            if (
                not self.auto_reload
                or cached_mtime == stat.st_mtime_ns
            ):
                return cached_text

        try:
            text = path.read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            raise PromptError(
                f"Cannot read prompt: {path}"
            ) from exc

        if not text:
            raise PromptError(
                f"Prompt is empty: {path}"
            )

        self._cache[name] = (
            stat.st_mtime_ns,
            text,
        )

        return text

    def reload(
        self,
        name: str | None = None,
    ) -> None:
        """
        Clear one prompt cache entry or the whole cache.

        Normally manual reload is not required because auto_reload=True.
        """

        if name is None:
            self._cache.clear()
            return

        self._validate_name(name)
        self._cache.pop(name, None)

    def list_prompts(
        self,
    ) -> list[str]:
        if not self.prompts_dir.is_dir():
            return []

        return sorted(
            path.stem
            for path in self.prompts_dir.glob("*.txt")
            if self._VALID_NAME.fullmatch(path.stem)
        )

    def path_for(
        self,
        name: str,
    ) -> Path:
        """
        Return the resolved .txt path for diagnostics/UI.
        """
        return self._resolve(name)

    def _resolve(
        self,
        name: str,
    ) -> Path:
        name = str(name).strip()
        self._validate_name(name)

        path = (
            self.prompts_dir
            / f"{name}.txt"
        ).resolve()

        try:
            path.relative_to(
                self.prompts_dir
            )
        except ValueError as exc:
            raise PromptError(
                "Prompt path escapes prompts directory."
            ) from exc

        return path

    def _validate_name(
        self,
        name: str,
    ) -> None:
        if not self._VALID_NAME.fullmatch(name):
            raise PromptError(
                "Invalid prompt name. Use only letters, numbers, _ and -."
            )
