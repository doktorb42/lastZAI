"""Review queue and tightly scoped promotion for durable player facts."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from config.runtime_config import DATA_DIR


class MemoryReviewStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"


@dataclass(slots=True)
class MemoryReviewCandidate:
    candidate_id: str
    player_uid: str
    memory_type: str
    content: str
    confidence: float
    source_message_key: str | None
    created_at: float
    status: MemoryReviewStatus
    expires_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "player_uid": self.player_uid,
            "memory_type": self.memory_type,
            "content": self.content,
            "confidence": self.confidence,
            "source_message_key": self.source_message_key,
            "created_at": self.created_at,
            "status": self.status.value,
            "expires_at": self.expires_at,
        }


class MemoryReviewStore:
    def __init__(self, path: str | Path = DATA_DIR / "databases" / "memory_review.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_review_candidates (
                candidate_id TEXT PRIMARY KEY,
                player_uid TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_message_key TEXT,
                created_at REAL NOT NULL,
                status TEXT NOT NULL,
                expires_at INTEGER
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def ingest(
        self,
        *,
        player_uid: str,
        memory_type: str,
        content: str,
        confidence: float,
        source_message_key: str | None,
        expires_at: int | None = None,
        conversation_store: Any | None = None,
        **_ignored: Any,
    ) -> MemoryReviewCandidate:
        candidate = MemoryReviewCandidate(
            candidate_id=uuid.uuid4().hex,
            player_uid=str(player_uid),
            memory_type=str(memory_type),
            content=" ".join(str(content or "").split()),
            confidence=max(0.0, min(1.0, float(confidence))),
            source_message_key=str(source_message_key) if source_message_key else None,
            created_at=time.time(),
            status=MemoryReviewStatus.PENDING,
            expires_at=expires_at,
        )
        if self._eligible_for_auto_promotion(candidate):
            candidate.status = MemoryReviewStatus.PROMOTED
            if conversation_store is not None:
                conversation_store.add_memory(
                    player_uid=candidate.player_uid,
                    memory_type=candidate.memory_type,
                    content=candidate.content,
                    importance=0.85,
                    confidence=candidate.confidence,
                    memory_key=_memory_key(candidate),
                    expires_at=candidate.expires_at,
                )
        self.conn.execute(
            """
            INSERT INTO memory_review_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                candidate.candidate_id, candidate.player_uid, candidate.memory_type,
                candidate.content, candidate.confidence, candidate.source_message_key,
                candidate.created_at, candidate.status.value, candidate.expires_at,
            ),
        )
        self.conn.commit()
        return candidate

    @staticmethod
    def _eligible_for_auto_promotion(candidate: MemoryReviewCandidate) -> bool:
        if candidate.confidence < 0.95 or candidate.memory_type != "identity":
            return False
        text = candidate.content.casefold()
        return any(marker in text for marker in ("my name is", "call me", "i am from", "i'm from"))


def _memory_key(candidate: MemoryReviewCandidate) -> str:
    if candidate.memory_type == "identity":
        return "identity.explicit"
    return f"{candidate.memory_type}.reviewed"
