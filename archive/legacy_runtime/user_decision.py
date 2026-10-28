"""Non-blocking lifecycle for the rare cases that need a human decision."""

from __future__ import annotations

import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class UserDecisionStatus(str, Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass(slots=True)
class UserDecisionRequest:
    request_type: str
    question: str
    reason: str
    options: list[str]
    related_goal_id: str | None = None
    related_action_id: str | None = None
    related_reply_id: str | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: UserDecisionStatus = UserDecisionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    resolution: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "request_type": self.request_type,
            "question": self.question,
            "reason": self.reason,
            "options": list(self.options),
            "related_goal_id": self.related_goal_id,
            "related_action_id": self.related_action_id,
            "related_reply_id": self.related_reply_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }


class UserDecisionManager:
    """Keeps requests idempotent; it never blocks the capture/event loop."""

    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self.now = now
        self._requests: dict[str, UserDecisionRequest] = {}
        self._commands: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def create_or_get(
        self,
        *,
        request_type: str,
        question: str,
        reason: str,
        options: list[str],
        related_goal_id: str | None,
        related_action_id: str | None,
        related_reply_id: str | None,
    ) -> tuple[UserDecisionRequest, bool]:
        for request in self._requests.values():
            if (
                request.status == UserDecisionStatus.PENDING
                and request.request_type == request_type
                and request.related_goal_id == related_goal_id
                and request.related_reply_id == related_reply_id
            ):
                return request, False
        request = UserDecisionRequest(
            request_type=request_type,
            question=question,
            reason=reason,
            options=list(options),
            related_goal_id=related_goal_id,
            related_action_id=related_action_id,
            related_reply_id=related_reply_id,
            created_at=self.now(),
        )
        self._requests[request.request_id] = request
        return request, True

    def get(self, request_id: str | None) -> UserDecisionRequest | None:
        return self._requests.get(str(request_id)) if request_id else None

    def pending(self) -> list[UserDecisionRequest]:
        return [
            request for request in self._requests.values()
            if request.status == UserDecisionStatus.PENDING
        ]

    def pending_delivery(self) -> UserDecisionRequest | None:
        return next(
            (
                request for request in self.pending()
                if request.request_type == "AMBIGUOUS_DELIVERY"
            ),
            None,
        )

    def resolve(self, request_id: str, resolution: str) -> UserDecisionRequest:
        request = self._require_pending(request_id)
        choice = str(resolution or "").strip().lower()
        if choice not in request.options:
            raise ValueError(f"Unsupported decision resolution: {resolution!r}")
        request.status = (
            UserDecisionStatus.CANCELLED if choice == "cancel" else UserDecisionStatus.RESOLVED
        )
        request.resolution = choice
        request.resolved_at = self.now()
        return request

    def expire(self, request_id: str) -> UserDecisionRequest:
        request = self._require_pending(request_id)
        request.status = UserDecisionStatus.EXPIRED
        request.resolved_at = self.now()
        return request

    def submit_terminal_command(self, line: str) -> None:
        parts = str(line or "").strip().split()
        if not parts or parts[0].lower() != "decision":
            return
        if len(parts) == 2 and parts[1].lower() == "list":
            self._commands.put(("list", None))
        elif len(parts) == 3 and parts[1].lower() in {"retry", "cancel", "assume-sent"}:
            self._commands.put((parts[1].lower(), parts[2]))

    def drain_terminal_commands(self) -> list[tuple[str, str | None]]:
        commands: list[tuple[str, str | None]] = []
        while True:
            try:
                commands.append(self._commands.get_nowait())
            except queue.Empty:
                return commands

    def snapshot(self) -> list[dict[str, object]]:
        return [request.to_dict() for request in sorted(self._requests.values(), key=lambda item: item.created_at)]

    def _require_pending(self, request_id: str) -> UserDecisionRequest:
        request = self.get(request_id)
        if request is None:
            raise KeyError(f"Unknown user decision request: {request_id}")
        if request.status != UserDecisionStatus.PENDING:
            raise ValueError(f"User decision request is {request.status.value}, not PENDING")
        return request


class TerminalDecisionControl:
    """Daemon stdin reader; command execution remains on the PlayerLoop thread."""

    def __init__(self, manager: UserDecisionManager) -> None:
        self.manager = manager
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running or not getattr(sys.stdin, "readable", lambda: False)():
            return
        self._running = True
        self._thread = threading.Thread(target=self._read, name="peva-decision-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _read(self) -> None:
        while self._running:
            line = sys.stdin.readline()
            if not line:
                self._running = False
                return
            self.manager.submit_terminal_command(line)
