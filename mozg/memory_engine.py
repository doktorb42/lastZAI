from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .conversation_store import ConversationStore, PlayerMemory
from .lastz_ai import AIReply, ChatTurn, LastZAI
from config.prompt_manager import PromptError
from config.runtime_config import CONVERSATION_DB_PATH


DEFAULT_DB_PATH = CONVERSATION_DB_PATH


# ======================================================================
# CONFIG / DATA
# ======================================================================


@dataclass(slots=True)
class MemoryEngineConfig:
    prompt_name: str = "memory_extract"

    max_existing_memories: int = 20
    recent_history_limit: int = 8
    max_candidates_per_message: int = 3

    min_importance: float = 0.35
    min_confidence: float = 0.65

    max_content_chars: int = 260
    max_key_chars: int = 80

    # Avoid wasting a model call on obvious filler.
    skip_trivial_messages: bool = True


@dataclass(slots=True)
class MemoryCandidate:
    memory_type: str
    memory_key: str
    content: str
    importance: float
    confidence: float


@dataclass(slots=True)
class MemoryAnalysis:
    analyzed: bool
    player_uid: str
    text: str

    candidates: list[MemoryCandidate] = field(default_factory=list)
    saved_ids: list[int] = field(default_factory=list)

    skipped_reason: str | None = None
    raw_model_output: str | None = None
    error: str | None = None

    @property
    def saved(self) -> bool:
        return bool(self.saved_ids)


# ======================================================================
# ENGINE
# ======================================================================


class MemoryEngine:
    """
    Long-term player-memory extractor.

    It deliberately does not generate chat replies.

    Flow:
        incoming message
            ↓
        deterministic trivial-message filter
            ↓
        existing memories + small recent history
            ↓
        prompts/memory_extract.txt
            ↓
        local LLM returns JSON
            ↓
        strict schema validation
            ↓
        ConversationStore.add_memory()
            ↓
        same memory_key updates old fact instead of duplicating it
    """

    ALLOWED_TYPES = {
        "preference",
        "schedule",
        "alliance",
        "relationship",
        "playstyle",
        "game_info",
        "goal",
        "strategy",
        "identity",
        "other",
    }

    MEMORY_KEY_RE = re.compile(
        r"^[a-z0-9][a-z0-9._-]*$"
    )

    # Only exact/simple filler is skipped. Short but meaningful statements
    # such as "I work nights" must still be analyzed.
    TRIVIAL_RE = re.compile(
        r"""(?ix)^
        (?:
            hi+ |
            hey+ |
            hello+ |
            yo+ |
            sup |
            lol+ |
            lmao+ |
            xd+ |
            ok(?:ay)? |
            k |
            yes |
            yea+h? |
            yep |
            no |
            nope |
            thanks? |
            thx |
            ty |
            gg |
            nice |
            cool |
            haha+ |
            hehe+
        )
        [!?.\s]*$
        """
    )

    def __init__(
        self,
        *,
        ai: LastZAI,
        store: ConversationStore,
        config: MemoryEngineConfig | None = None,
    ):
        self.ai = ai
        self.store = store
        self.config = config or MemoryEngineConfig()

    # ------------------------------------------------------------------
    # MAIN API
    # ------------------------------------------------------------------

    def analyze_text(
        self,
        *,
        player_uid: str,
        text: str,
        player_name: str | None = None,
        room_id: str | None = None,
        source_message_id: int | None = None,
        save: bool = False,
    ) -> MemoryAnalysis:

        player_uid = str(player_uid or "").strip()
        text = str(text or "").strip()

        if not player_uid:
            return MemoryAnalysis(
                analyzed=False,
                player_uid="",
                text=text,
                error="player_uid is empty.",
            )

        if not text:
            return MemoryAnalysis(
                analyzed=False,
                player_uid=player_uid,
                text="",
                skipped_reason="empty_message",
            )

        if (
            self.config.skip_trivial_messages
            and self._is_trivial(text)
        ):
            return MemoryAnalysis(
                analyzed=False,
                player_uid=player_uid,
                text=text,
                skipped_reason="trivial_message",
            )

        existing = self.store.get_memories(
            player_uid=player_uid,
            limit=self.config.max_existing_memories,
            min_importance=0.0,
            include_inactive=False,
            mark_used=False,
        )

        history = self.store.get_history(
            room_id=room_id,
            player_uid=(
                None
                if room_id
                else player_uid
            ),
            limit=self.config.recent_history_limit,
        )

        try:
            system_prompt = self.ai.prompts.get(
                self.config.prompt_name
            )
        except (
            AttributeError,
            PromptError,
        ) as exc:
            return MemoryAnalysis(
                analyzed=False,
                player_uid=player_uid,
                text=text,
                error=f"Cannot load memory prompt: {exc}",
            )

        user_prompt = self._build_user_prompt(
            player_uid=player_uid,
            player_name=player_name,
            text=text,
            existing_memories=existing,
            history=history,
        )

        reply = self.ai.complete_task_safe(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        if reply.error:
            return MemoryAnalysis(
                analyzed=True,
                player_uid=player_uid,
                text=text,
                raw_model_output=reply.text or None,
                error=reply.error,
            )

        try:
            payload = self._parse_json_object(
                reply.text
            )
        except ValueError as exc:
            return MemoryAnalysis(
                analyzed=True,
                player_uid=player_uid,
                text=text,
                raw_model_output=reply.text,
                error=str(exc),
            )

        candidates = self._validate_candidates(
            payload,
            existing_memories=existing,
        )

        result = MemoryAnalysis(
            analyzed=True,
            player_uid=player_uid,
            text=text,
            candidates=candidates,
            raw_model_output=reply.text,
        )

        if not save:
            return result

        for candidate in candidates:
            memory_id = self.store.add_memory(
                player_uid=player_uid,
                memory_type=candidate.memory_type,
                memory_key=candidate.memory_key,
                content=candidate.content,
                importance=candidate.importance,
                confidence=candidate.confidence,
                source_message_id=source_message_id,
            )

            result.saved_ids.append(
                memory_id
            )

        return result

    def analyze_chat_message(
        self,
        message: Any,
        *,
        save: bool = False,
    ) -> MemoryAnalysis:
        """
        Convenience wrapper for chat_capture.ChatMessage.

        If the message is already stored in conversation.db, the source DB id
        is linked to any saved memory.
        """

        source_key = getattr(
            message,
            "source_message_key",
            None,
        )

        stored_message = None

        if source_key:
            stored_message = (
                self.store.get_message_by_source_key(
                    str(source_key)
                )
            )

        return self.analyze_text(
            player_uid=str(
                getattr(
                    message,
                    "sender_uid",
                    "",
                )
                or ""
            ),
            player_name=(
                getattr(
                    message,
                    "sender_name",
                    None,
                )
            ),
            room_id=(
                getattr(
                    message,
                    "room_id",
                    None,
                )
            ),
            text=str(
                getattr(
                    message,
                    "text",
                    "",
                )
                or ""
            ),
            source_message_id=(
                stored_message.id
                if stored_message is not None
                else None
            ),
            save=save,
        )

    # ------------------------------------------------------------------
    # PROMPT BUILDING
    # ------------------------------------------------------------------

    def _build_user_prompt(
        self,
        *,
        player_uid: str,
        player_name: str | None,
        text: str,
        existing_memories: Sequence[PlayerMemory],
        history: Sequence[ChatTurn],
    ) -> str:

        existing_payload = [
            {
                "memory_type": item.memory_type,
                "memory_key": item.memory_key,
                "content": item.content,
                "importance": item.importance,
                "confidence": item.confidence,
            }
            for item in existing_memories
        ]

        history_payload = [
            {
                "role": turn.role,
                "content": turn.content,
            }
            for turn in history
        ]

        input_payload = {
            "player": {
                "uid": player_uid,
                "name": (
                    str(player_name).strip()
                    if player_name
                    else None
                ),
            },
            "existing_memories": existing_payload,
            "recent_history": history_payload,
            "new_player_message": text,
        }

        return (
            "Analyze the following JSON data and return only the "
            "required JSON result.\n\n"
            + json.dumps(
                input_payload,
                ensure_ascii=False,
                indent=2,
            )
        )

    # ------------------------------------------------------------------
    # MODEL OUTPUT PARSING
    # ------------------------------------------------------------------

    @classmethod
    def _parse_json_object(
        cls,
        text: str,
    ) -> dict[str, Any]:

        text = str(text or "").strip()

        if not text:
            raise ValueError(
                "Memory model returned an empty response."
            )

        # Common local-model behavior:
        # ```json
        # {...}
        # ```
        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            )
            text = re.sub(
                r"\s*```$",
                "",
                text,
            ).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Conservative fallback: extract exactly one outer JSON object.
            start = text.find("{")
            end = text.rfind("}")

            if (
                start < 0
                or end <= start
            ):
                raise ValueError(
                    "Memory model did not return valid JSON."
                )

            candidate = text[
                start:end + 1
            ]

            try:
                parsed = json.loads(
                    candidate
                )
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Memory model returned malformed JSON."
                ) from exc

        if not isinstance(
            parsed,
            dict,
        ):
            raise ValueError(
                "Memory model JSON must be an object."
            )

        return parsed

    def _validate_candidates(
        self,
        payload: Mapping[str, Any],
        *,
        existing_memories: Sequence[PlayerMemory],
    ) -> list[MemoryCandidate]:

        raw_memories = payload.get(
            "memories",
            [],
        )

        if not isinstance(
            raw_memories,
            list,
        ):
            return []

        existing_by_key = {
            item.memory_key: item
            for item in existing_memories
            if item.memory_key
        }

        existing_content = {
            self._normalize_fact(
                item.content
            )
            for item in existing_memories
        }

        accepted: list[
            MemoryCandidate
        ] = []

        used_keys: set[str] = set()

        for raw in raw_memories:
            if len(
                accepted
            ) >= self.config.max_candidates_per_message:
                break

            candidate = self._validate_one(
                raw
            )

            if candidate is None:
                continue

            if candidate.memory_key in used_keys:
                continue

            used_keys.add(
                candidate.memory_key
            )

            old = existing_by_key.get(
                candidate.memory_key
            )

            if old is not None:
                # Exact same fact under the same key is not worth rewriting.
                if (
                    self._normalize_fact(old.content)
                    == self._normalize_fact(candidate.content)
                    and abs(
                        old.importance
                        - candidate.importance
                    ) < 0.05
                    and abs(
                        old.confidence
                        - candidate.confidence
                    ) < 0.05
                ):
                    continue

            elif (
                self._normalize_fact(
                    candidate.content
                )
                in existing_content
            ):
                # Same content under another key -> avoid duplicate memory.
                continue

            accepted.append(
                candidate
            )

        return accepted

    def _validate_one(
        self,
        raw: Any,
    ) -> MemoryCandidate | None:

        if not isinstance(
            raw,
            Mapping,
        ):
            return None

        memory_type = str(
            raw.get(
                "memory_type",
                "",
            )
            or ""
        ).strip().lower()

        if memory_type not in self.ALLOWED_TYPES:
            return None

        memory_key = str(
            raw.get(
                "memory_key",
                "",
            )
            or ""
        ).strip().lower()

        if (
            not memory_key
            or len(memory_key)
            > self.config.max_key_chars
            or not self.MEMORY_KEY_RE.fullmatch(
                memory_key
            )
        ):
            return None

        content = str(
            raw.get(
                "content",
                "",
            )
            or ""
        ).strip()

        content = " ".join(
            content.split()
        )

        if (
            len(content) < 6
            or len(content)
            > self.config.max_content_chars
        ):
            return None

        importance = self._score_or_none(
            raw.get(
                "importance"
            )
        )
        confidence = self._score_or_none(
            raw.get(
                "confidence"
            )
        )

        if (
            importance is None
            or confidence is None
        ):
            return None

        if (
            importance
            < self.config.min_importance
        ):
            return None

        if (
            confidence
            < self.config.min_confidence
        ):
            return None

        return MemoryCandidate(
            memory_type=memory_type,
            memory_key=memory_key,
            content=content,
            importance=importance,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # FILTERS / HELPERS
    # ------------------------------------------------------------------

    @classmethod
    def _is_trivial(
        cls,
        text: str,
    ) -> bool:
        text = " ".join(
            str(text).strip().split()
        )

        if len(text) <= 1:
            return True

        return bool(
            cls.TRIVIAL_RE.fullmatch(
                text
            )
        )

    @staticmethod
    def _score_or_none(
        value: Any,
    ) -> float | None:
        try:
            value = float(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return None

        if not 0.0 <= value <= 1.0:
            return None

        return value

    @staticmethod
    def _normalize_fact(
        text: str,
    ) -> str:
        return " ".join(
            str(text)
            .strip()
            .lower()
            .split()
        )


# ======================================================================
# SELF TEST
# ======================================================================


class _FakePrompts:
    def get(
        self,
        name: str,
    ) -> str:
        if name != "memory_extract":
            raise PromptError(
                f"Unknown fake prompt: {name}"
            )

        return "Return JSON only."


class _FakeAI:
    def __init__(self):
        self.prompts = _FakePrompts()

    def complete_task_safe(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> AIReply:
        if (
            "late shifts"
            in user_prompt
        ):
            return AIReply(
                text=json.dumps(
                    {
                        "memories": [
                            {
                                "memory_type": "schedule",
                                "memory_key": "work.schedule",
                                "content": (
                                    "Player often works late shifts "
                                    "and may miss evening alliance events."
                                ),
                                "importance": 0.85,
                                "confidence": 0.95,
                            }
                        ]
                    }
                )
            )

        return AIReply(
            text='{"memories":[]}'
        )


def _self_test() -> None:
    test_db = Path(
        __file__
    ).with_name(
        "memory_engine_test.db"
    )

    for candidate in (
        test_db,
        Path(str(test_db) + "-wal"),
        Path(str(test_db) + "-shm"),
    ):
        if candidate.exists():
            candidate.unlink()

    uid = "1349515331000772"

    with ConversationStore(
        test_db
    ) as store:
        engine = MemoryEngine(
            ai=_FakeAI(),  # type: ignore[arg-type]
            store=store,
        )

        trivial = engine.analyze_text(
            player_uid=uid,
            player_name="OG doktorb",
            text="hello",
            save=True,
        )

        print(
            "Trivial analyzed:",
            trivial.analyzed,
        )
        print(
            "Trivial reason:",
            trivial.skipped_reason,
        )

        useful = engine.analyze_text(
            player_uid=uid,
            player_name="OG doktorb",
            text=(
                "I work late shifts so I usually "
                "miss Tyrant in the evening."
            ),
            save=True,
        )

        print(
            "Useful candidates:",
            len(useful.candidates),
        )
        print(
            "Saved IDs:",
            useful.saved_ids,
        )

        memories = store.get_memories(
            player_uid=uid,
            limit=10,
            min_importance=0.0,
        )

        print(
            "Stored memories:"
        )

        for memory in memories:
            print(
                " ",
                memory.memory_key,
                "->",
                memory.content,
            )

        if len(memories) != 1:
            raise RuntimeError(
                "Expected exactly one stored memory."
            )

        # Same key updates, it does not create a second row.
        second = engine.analyze_text(
            player_uid=uid,
            player_name="OG doktorb",
            text=(
                "I still work late shifts and often "
                "miss evening events."
            ),
            save=True,
        )

        memories_after = store.get_memories(
            player_uid=uid,
            limit=10,
            min_importance=0.0,
        )

        print(
            "Memories after update:",
            len(memories_after),
        )

        if len(memories_after) != 1:
            raise RuntimeError(
                "Same memory_key should update, not duplicate."
            )

    print()
    print("SELF-TEST OK")


# ======================================================================
# CLI
# ======================================================================


def _print_analysis(
    result: MemoryAnalysis,
) -> None:
    print(
        "Analyzed:",
        result.analyzed,
    )

    if result.skipped_reason:
        print(
            "Skipped:",
            result.skipped_reason,
        )

    if result.error:
        print(
            "Error:",
            result.error,
        )

    print(
        "Candidates:",
        len(result.candidates),
    )

    for candidate in result.candidates:
        print(
            "-",
            candidate.memory_type,
            "|",
            candidate.memory_key,
        )
        print(
            " ",
            candidate.content,
        )
        print(
            " ",
            f"importance={candidate.importance:.2f}",
            f"confidence={candidate.confidence:.2f}",
        )

    if result.saved_ids:
        print(
            "Saved IDs:",
            result.saved_ids,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "PEva long-term player memory engine."
        )
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--text",
        help=(
            "Analyze one text with the real local model."
        ),
    )

    parser.add_argument(
        "--uid",
        default=None,
        help=(
            "Player UID to analyze. Required with --text; no real-player "
            "UID is used as an implicit default."
        ),
    )

    parser.add_argument(
        "--name",
        default=None,
    )

    parser.add_argument(
        "--room-id",
        default=None,
    )

    parser.add_argument(
        "--save",
        action="store_true",
        help=(
            "Actually write accepted memories to conversation.db. "
            "Without this flag the command is preview-only."
        ),
    )

    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

    if not args.text:
        parser.print_help()
        return

    if not args.uid:
        parser.error("--uid is required when using --text")

    ai = LastZAI()

    if not ai.health_check():
        raise SystemExit(
            "Local llama-server is not running."
        )

    with ConversationStore(
        DEFAULT_DB_PATH
    ) as store:
        engine = MemoryEngine(
            ai=ai,
            store=store,
        )

        result = engine.analyze_text(
            player_uid=args.uid,
            player_name=args.name,
            room_id=args.room_id,
            text=args.text,
            save=args.save,
        )

        _print_analysis(
            result
        )

        if result.raw_model_output:
            print()
            print(
                "Raw model output:"
            )
            print(
                result.raw_model_output
            )


if __name__ == "__main__":
    main()
