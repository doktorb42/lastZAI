"""Explicit loopback-only client for an already-running local llama-server.

It has no database dependency and cannot mutate conversation state. The service
owns all persistence and calls this client only after an explicit operator action.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping


class LocalModelError(RuntimeError):
    """The explicitly configured local model endpoint failed."""


@dataclass(frozen=True)
class ModelCompletion:
    text: str
    provenance: dict[str, Any]


def normalize_loopback_chat_endpoint(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or not parsed.hostname:
        raise LocalModelError("model URL must be an explicit http:// loopback OpenAI-compatible URL")
    if parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}:
        raise LocalModelError("model URL must point to localhost, 127.0.0.1, or ::1")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LocalModelError("model URL cannot contain credentials, a query, or a fragment")
    base = value.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


class LocalLlamaClient:
    """Minimal OpenAI-compatible client for the existing local model only."""

    def __init__(
        self,
        *,
        endpoint: str,
        model_label: str = "lastz-action-v1-prompted",
        timeout_seconds: float = 90.0,
    ):
        self.endpoint = normalize_loopback_chat_endpoint(endpoint)
        self.model_label = model_label
        self.timeout_seconds = timeout_seconds
        if not 5.0 <= timeout_seconds <= 600.0:
            raise LocalModelError("timeout_seconds must be between 5 and 600")

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> ModelCompletion:
        if not 8 <= max_tokens <= 512:
            raise LocalModelError("max_tokens must be between 8 and 512")
        if not 0.0 <= temperature <= 2.0:
            raise LocalModelError("temperature must be between 0 and 2")
        payload = {
            "model": self.model_label,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_payload = response.read().decode("utf-8")
                response_payload = json.loads(raw_payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LocalModelError(f"local model returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LocalModelError(f"local model request failed: {exc}") from exc

        if not isinstance(response_payload, Mapping):
            raise LocalModelError("local model response must be an object")
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise LocalModelError("local model response has no choices[0]")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise LocalModelError("local model response has no choices[0].message")
        content = message.get("content")
        if not isinstance(content, str):
            raise LocalModelError("local model response content must be text")
        usage = response_payload.get("usage")
        return ModelCompletion(
            text=content,
            provenance={
                "provider": "local_llama_server",
                "endpoint": self.endpoint,
                "model_label": self.model_label,
                "usage": dict(usage) if isinstance(usage, Mapping) else None,
            },
        )

