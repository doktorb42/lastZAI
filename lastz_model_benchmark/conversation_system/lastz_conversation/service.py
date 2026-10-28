"""Orchestration layer for capture, draft review, memory proposals, and export."""

from __future__ import annotations

import json
import inspect
from pathlib import Path
from typing import Any, Mapping, Protocol

from .contracts import (
    MEMORY_PROMPT_VERSION,
    PrivateChatObservation,
    canonical_json,
    render_memory_messages,
    validate_player_reply,
)
from .database import ConversationRepository, RepositoryError, TurnStateError
from .memory_updates import MemoryUpdateError, parse_and_validate_memory_update
from .model_client import ModelCompletion


class CompletionClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> ModelCompletion: ...


class ConversationService:
    """Python-owned gateway between a local model and durable player memory.

    The service has no ADB import and no method that sends a game action. The
    only delivery artefact it can return is a validated handoff dictionary for a
    future executor integration, after human approval.
    """

    def __init__(self, database_path: Path | str):
        self.repository = ConversationRepository(database_path)

    def close(self) -> None:
        self.repository.close()

    def enroll_verified_player(self, **kwargs: Any) -> dict[str, str]:
        return self.repository.enroll_verified_player(**kwargs)

    def bind_external_route(self, **kwargs: Any) -> dict[str, Any]:
        """Persist verified Last Z routing metadata; this never calls a game API."""

        return self.repository.bind_external_route(**kwargs)

    def capture_incoming(self, observation: PrivateChatObservation) -> dict[str, Any]:
        return self.repository.ingest_observation(observation)

    def lifecycle(self, turn_id: str) -> dict[str, Any]:
        """Read lifecycle state; this has no model or executor side effect."""

        return self.repository.lifecycle_for_turn(turn_id)

    def draft_prompt(self, turn_id: str) -> list[dict[str, str]]:
        # Read the immutable task snapshot rather than reconstructing a prompt
        # from mutable player state.  This is the exact Chat V2 input eligible
        # for later review/export/training.
        return list(self.repository.chat_task_snapshot_for_turn(turn_id)["messages_json"])

    def _runtime_identifier_leaks(self, *, turn_id: str, text: Any) -> bool:
        """Check a completion without ever adding route/database values to a prompt."""

        if not isinstance(text, str):
            return False
        return any(identifier in text for identifier in self.repository.runtime_identifiers_for_turn(turn_id))

    def _reject_reply_identifier_leak(self, *, turn_id: str, raw_reply: Any, verdict: Mapping[str, Any]) -> dict[str, Any]:
        """Turn a private runtime-ID echo into a hard player-ready rejection."""

        result = dict(verdict)
        if self._runtime_identifier_leaks(turn_id=turn_id, text=raw_reply):
            errors = list(result.get("errors", []))
            errors.append("forbidden_runtime_identifier_in_reply")
            result["errors"] = list(dict.fromkeys(str(error) for error in errors))
            result["valid"] = False
        return result

    def record_model_draft(
        self,
        *,
        turn_id: str,
        raw_reply: str,
        model_provenance: Mapping[str, Any],
    ) -> dict[str, Any]:
        turn = self.repository.get_turn(turn_id)
        verdict = self._reject_reply_identifier_leak(
            turn_id=turn_id,
            raw_reply=raw_reply,
            verdict=validate_player_reply(raw_reply, turn["context_json"]),
        )
        self.repository.record_generation(
            turn_id=turn_id,
            raw_reply=raw_reply,
            verdict=verdict,
            model_provenance=model_provenance,
        )
        return dict(verdict)

    def generate_draft(
        self,
        *,
        turn_id: str,
        client: CompletionClient,
        max_tokens: int = 180,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        messages = self.draft_prompt(turn_id)
        try:
            completion = client.complete(messages, max_tokens=max_tokens, temperature=temperature)
        except Exception as exc:
            self.repository.mark_generation_error(turn_id, str(exc))
            raise
        verdict = self.record_model_draft(
            turn_id=turn_id,
            raw_reply=completion.text,
            model_provenance=completion.provenance,
        )
        return {
            "turn_id": turn_id,
            "valid": bool(verdict["valid"]),
            "normalized_reply": verdict["normalized_text"],
            "errors": list(verdict["errors"]),
            "warnings": list(verdict["warnings"]),
        }

    def approve_reply(self, *, turn_id: str, approved_reply: str, reviewer: str) -> dict[str, Any]:
        turn = self.repository.get_turn(turn_id)
        verdict = self._reject_reply_identifier_leak(
            turn_id=turn_id,
            raw_reply=approved_reply,
            verdict=validate_player_reply(approved_reply, turn["context_json"]),
        )
        self.repository.approve_turn(
            turn_id=turn_id,
            approved_reply=approved_reply,
            verdict=verdict,
            reviewer=reviewer,
        )
        return dict(verdict)

    def reject_reply(self, *, turn_id: str, reviewer: str, reason: str) -> None:
        self.repository.reject_turn(turn_id=turn_id, reviewer=reviewer, reason=reason)

    def memory_prompt(self, turn_id: str) -> list[dict[str, str]]:
        snapshot = self.repository.ensure_memory_task_snapshot(turn_id)
        return list(snapshot["messages_json"])

    def _parse_memory_output(self, *, raw_output: str, turn_id: str):
        """Use V2 aliases, with a temporary read-only bridge for pilot data."""

        if self._runtime_identifier_leaks(turn_id=turn_id, text=raw_output):
            raise MemoryUpdateError("model memory output exposes a forbidden runtime identifier")
        refs = self.repository.allowed_refs_for_turn(turn_id)
        parameters = inspect.signature(parse_and_validate_memory_update).parameters
        if "allowed_message_refs" in parameters:
            return parse_and_validate_memory_update(
                raw_output,
                allowed_message_refs=refs["message_refs"],
                allowed_memory_refs=refs["memory_refs"],
                allowed_thread_refs=refs["thread_refs"],
                allowed_promise_refs=refs["promise_refs"],
            )
        # Compatibility only while opening an old V1 pilot database.  The V2
        # rendered prompt never contains these raw IDs.
        ids = self.repository.allowed_ids_for_turn(turn_id)
        return parse_and_validate_memory_update(
            raw_output,
            allowed_message_ids=ids["message_ids"],
            allowed_memory_ids=ids["memory_ids"],
            allowed_thread_ids=ids["thread_ids"],
            allowed_promise_ids=ids["promise_ids"],
        )

    def record_memory_output(
        self,
        *,
        turn_id: str,
        raw_output: str,
        model_provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        messages = self.memory_prompt(turn_id)
        task_snapshot = self.repository.ensure_memory_task_snapshot(turn_id)
        try:
            validated = self._parse_memory_output(raw_output=raw_output, turn_id=turn_id)
        except MemoryUpdateError as exc:
            attempt_id = self.repository.record_memory_attempt(
                turn_id=turn_id,
                memory_messages=messages,
                raw_output=raw_output,
                parsed=None,
                validation={"valid": False, "errors": [str(exc)]},
                status="rejected",
                model_provenance=model_provenance,
                task_snapshot_id=str(task_snapshot["task_snapshot_id"]),
            )
            return {"attempt_id": attempt_id, "valid": False, "errors": [str(exc)]}
        canonical = validated.as_json()
        attempt_id = self.repository.record_memory_attempt(
            turn_id=turn_id,
            memory_messages=messages,
            raw_output=raw_output,
            parsed=canonical,
            validation={"valid": True, "operations": len(validated.operations)},
            status="validated",
            model_provenance=model_provenance,
            task_snapshot_id=str(task_snapshot["task_snapshot_id"]),
        )
        return {
            "attempt_id": attempt_id,
            "valid": True,
            "operations": len(validated.operations),
            "proposal": canonical,
        }

    def generate_memory_proposal(
        self,
        *,
        turn_id: str,
        client: CompletionClient,
        max_tokens: int = 320,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        messages = self.memory_prompt(turn_id)
        completion = client.complete(messages, max_tokens=max_tokens, temperature=temperature)
        return self.record_memory_output(
            turn_id=turn_id,
            raw_output=completion.text,
            model_provenance=completion.provenance,
        )

    def apply_memory_proposal(self, *, attempt_id: str, applied_by: str) -> None:
        attempt = self.repository.get_memory_attempt(attempt_id)
        if attempt["status"] != "validated" or not isinstance(attempt["parsed_json"], dict):
            raise TurnStateError("memory proposal is not a validated JSON update")
        # Validate a second time immediately before the only mutation path.
        # This defends against a stale/corrupted audit row and makes the
        # pre-save validation guarantee explicit rather than historical.
        validated = self._parse_memory_output(
            raw_output=canonical_json(attempt["parsed_json"]),
            turn_id=str(attempt["turn_id"]),
        )
        self.repository.apply_validated_memory_update(
            attempt_id=attempt_id,
            operations=list(validated.operations),
            applied_by=applied_by,
        )

    def executor_handoff(self, turn_id: str) -> dict[str, str]:
        """Return a safe payload; deliberately never invokes action_ex or ADB."""

        turn = self.repository.get_turn(turn_id)
        if turn["approval_status"] != "approved":
            raise TurnStateError("an unapproved reply cannot reach the executor")
        if turn["memory_status"] != "applied":
            raise TurnStateError("apply or explicitly review the validated memory update before delivery")
        approved = turn["approved_reply"]
        if not isinstance(approved, str):
            raise TurnStateError("approved reply is missing")
        verdict = self._reject_reply_identifier_leak(
            turn_id=turn_id,
            raw_reply=approved,
            verdict=validate_player_reply(approved, turn["context_json"]),
        )
        if not verdict["valid"]:
            raise TurnStateError("approved reply no longer passes the player-ready gate")
        route = self.repository.external_route_for_turn(turn_id)
        if route is None:
            # Compatibility for the isolated pilot.  Real Last Z routing must
            # be explicitly bound before a future root-runtime bridge is used.
            return {"action": "send_message", "text": verdict["normalized_text"]}
        if route["verification_state"] != "verified":
            raise TurnStateError("an unverified external route cannot receive a reply")
        return {
            "action": "reply_private",
            "reply_id": turn_id,
            "room_id": str(route["external_room_id"]),
            "channel": str(route["channel"]),
            "target_uid": str(route["counterpart_game_player_id"]),
            "text": verdict["normalized_text"],
        }

    def record_delivery_confirmation(
        self,
        *,
        turn_id: str,
        delivery_key: str,
        delivered_at: str,
    ) -> dict[str, str]:
        """Persist an external executor's success; this method never sends."""

        # Re-run the handoff gate before accepting a delivery record. This
        # prevents a caller from adding an outgoing message for a draft that
        # could no longer be legally delivered.
        self.executor_handoff(turn_id)
        return self.repository.record_delivery(
            turn_id=turn_id,
            delivery_key=delivery_key,
            delivered_at=delivered_at,
        )

    def exportable_turns(self) -> list[dict[str, Any]]:
        return self.repository.exportable_turns()

