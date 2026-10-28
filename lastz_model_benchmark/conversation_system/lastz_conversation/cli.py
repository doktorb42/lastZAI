"""Operator CLI for the local-only Player Memory + Conversation System.

No command imports ADB, starts BlueStacks, starts llama-server, or sends a game
action. A local model is called only by explicit generate commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .contracts import PrivateChatObservation, utc_now
from .database import RepositoryError
from .exporter import export_approved_examples
from .model_client import LocalLlamaClient
from .service import ConversationService


SYSTEM_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = SYSTEM_ROOT / "data"


class CliError(ValueError):
    """A CLI path or argument would exceed the isolated bundle scope."""


def _safe_inside_system(value: str | None, *, default: Path) -> Path:
    raw = Path(value) if value else default
    candidate = raw if raw.is_absolute() else SYSTEM_ROOT / raw
    resolved = candidate.resolve()
    try:
        resolved.relative_to(SYSTEM_ROOT.resolve())
    except ValueError as exc:
        raise CliError(f"path must remain inside {SYSTEM_ROOT}") from exc
    return resolved


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _client(args: argparse.Namespace) -> LocalLlamaClient:
    return LocalLlamaClient(
        endpoint=args.model_url,
        model_label=args.model_label,
        timeout_seconds=args.timeout_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Last Z Player Memory + Conversation System. No game action is executed."
    )
    parser.add_argument(
        "--db",
        help="SQLite path relative to conversation_system/ (default: data/lastz_player_memory.sqlite3).",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("init", help="Create/validate the isolated SQLite schema.")

    enroll = subcommands.add_parser("enroll-player", help="Create a manually verified stable player binding.")
    enroll.add_argument("--nickname", required=True)
    enroll.add_argument("--identity-type", choices=("game_player_id", "operator_binding"), required=True)
    enroll.add_argument("--identity-value", required=True)
    enroll.add_argument("--player-id")
    enroll.add_argument("--language")
    enroll.add_argument("--alliance")
    enroll.add_argument("--alliance-role")
    enroll.add_argument("--relationship-style")
    enroll.add_argument("--external-room-id", help="Verified Last Z room ID; storage/routing only, never prompt data.")
    enroll.add_argument("--channel", choices=("private",), default="private")
    enroll.add_argument("--counterpart-game-player-id", help="Verified counterpart UID for --external-room-id.")
    enroll.add_argument("--peva-game-player-id", help="Optional PEva game UID for future delivery routing.")
    enroll.add_argument("--verification-state", choices=("pending", "verified", "rejected"), default="verified")

    bind_route = subcommands.add_parser(
        "bind-route",
        help="Bind or revise verified Last Z routing metadata; never calls a game client.",
    )
    bind_route.add_argument("--conversation-id", required=True)
    bind_route.add_argument("--external-room-id", required=True)
    bind_route.add_argument("--channel", choices=("private",), default="private")
    bind_route.add_argument("--counterpart-game-player-id", required=True)
    bind_route.add_argument("--peva-game-player-id")
    bind_route.add_argument("--verification-state", choices=("pending", "verified", "rejected"), default="verified")
    bind_route.add_argument("--reason", default="operator_verified_binding")

    capture = subcommands.add_parser("capture", help="Persist one incoming message from first private-chat person only.")
    capture.add_argument("--person-index", type=int, default=0)
    capture.add_argument("--identity-type", choices=("game_player_id", "operator_binding"), required=True)
    capture.add_argument("--identity-value", required=True)
    capture.add_argument("--nickname", required=True)
    capture.add_argument("--incoming-text", required=True)
    capture.add_argument("--source-message-key", required=True)
    capture.add_argument("--source-message-id")
    capture.add_argument("--observed-at", default=utc_now())
    capture.add_argument("--observation-confidence", type=float, default=1.0)
    capture.add_argument("--capture-fingerprint")
    capture.add_argument("--external-room-id")
    capture.add_argument("--channel", choices=("private",), default="private")
    capture.add_argument("--peva-game-player-id")
    capture.add_argument("--verification-state", choices=("pending", "verified", "rejected"))

    draft_prompt = subcommands.add_parser("draft-prompt", help="Print the exact stored TASK_CHAT prompt for a turn.")
    draft_prompt.add_argument("--turn-id", required=True)

    generate = subcommands.add_parser("generate-draft", help="Explicitly call an already-running local model for TASK_CHAT.")
    generate.add_argument("--turn-id", required=True)
    generate.add_argument("--model-url", required=True)
    generate.add_argument("--model-label", default="lastz-action-v1-prompted")
    generate.add_argument("--timeout-seconds", type=float, default=90.0)
    generate.add_argument("--max-tokens", type=int, default=180)
    generate.add_argument("--temperature", type=float, default=0.2)

    approve = subcommands.add_parser("approve", help="Human-approve or edit a player-ready reply.")
    approve.add_argument("--turn-id", required=True)
    approve.add_argument("--reply", required=True)
    approve.add_argument("--reviewer", required=True)

    reject = subcommands.add_parser("reject", help="Human-reject a draft.")
    reject.add_argument("--turn-id", required=True)
    reject.add_argument("--reviewer", required=True)
    reject.add_argument("--reason", required=True)

    memory_prompt = subcommands.add_parser("memory-prompt", help="Print the exact stored TASK_MEMORY prompt for an approved turn.")
    memory_prompt.add_argument("--turn-id", required=True)

    memory = subcommands.add_parser("generate-memory", help="Explicitly call an already-running local model for TASK_MEMORY.")
    memory.add_argument("--turn-id", required=True)
    memory.add_argument("--model-url", required=True)
    memory.add_argument("--model-label", default="lastz-action-v1-prompted")
    memory.add_argument("--timeout-seconds", type=float, default=90.0)
    memory.add_argument("--max-tokens", type=int, default=320)
    memory.add_argument("--temperature", type=float, default=0.0)

    record_memory = subcommands.add_parser("record-memory-output", help="Validate/store an already captured TASK_MEMORY raw output.")
    record_memory.add_argument("--turn-id", required=True)
    record_memory.add_argument("--raw-output", required=True)

    apply_memory = subcommands.add_parser("apply-memory", help="Explicitly apply a Python-validated memory proposal.")
    apply_memory.add_argument("--attempt-id", required=True)
    apply_memory.add_argument("--reviewer", required=True)

    handoff = subcommands.add_parser("handoff", help="Return a validated executor payload only; never sends it.")
    handoff.add_argument("--turn-id", required=True)

    delivery = subcommands.add_parser(
        "record-delivery",
        help="Record a separately confirmed executor success; never invokes the executor.",
    )
    delivery.add_argument("--turn-id", required=True)
    delivery.add_argument("--delivery-key", required=True)
    delivery.add_argument("--delivered-at", default=utc_now())

    export = subcommands.add_parser("export-approved", help="Export eligible live captures; never starts training.")
    export.add_argument("--output-dir", help="Relative output directory inside conversation_system/ (default: data/exports).")

    quarantine = subcommands.add_parser("list-quarantine", help="List observations blocked by identity safety gates.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_path = _safe_inside_system(args.db, default=DATA_ROOT / "lastz_player_memory.sqlite3")
    service = ConversationService(database_path)
    try:
        if args.command == "init":
            _print({"status": "ready", "database": str(database_path), "guarantees": ["no model call", "no game action"]})
            return 0
        if args.command == "enroll-player":
            _print(
                service.enroll_verified_player(
                    nickname=args.nickname,
                    identity_type=args.identity_type,
                    identity_value=args.identity_value,
                    player_id=args.player_id,
                    language=args.language,
                    alliance_name=args.alliance,
                    alliance_role=args.alliance_role,
                    relationship_style=args.relationship_style,
                    external_room_id=args.external_room_id,
                    channel=args.channel,
                    counterpart_game_player_id=args.counterpart_game_player_id,
                    peva_game_player_id=args.peva_game_player_id,
                    verification_state=args.verification_state,
                )
            )
            return 0
        if args.command == "bind-route":
            _print(
                service.bind_external_route(
                    conversation_id=args.conversation_id,
                    external_room_id=args.external_room_id,
                    channel=args.channel,
                    counterpart_game_player_id=args.counterpart_game_player_id,
                    peva_game_player_id=args.peva_game_player_id,
                    verification_state=args.verification_state,
                    reason=args.reason,
                )
            )
            return 0
        if args.command == "capture":
            observation = PrivateChatObservation(
                person_index=args.person_index,
                identity_type=args.identity_type,
                identity_value=args.identity_value,
                nickname=args.nickname,
                incoming_text=args.incoming_text,
                source_message_key=args.source_message_key,
                source_message_id=args.source_message_id,
                observed_at=args.observed_at,
                observation_confidence=args.observation_confidence,
                capture_fingerprint=args.capture_fingerprint,
                external_room_id=args.external_room_id,
                channel=args.channel,
                peva_game_player_id=args.peva_game_player_id,
                verification_state=args.verification_state,
            )
            _print(service.capture_incoming(observation))
            return 0
        if args.command == "draft-prompt":
            _print(service.draft_prompt(args.turn_id))
            return 0
        if args.command == "generate-draft":
            _print(
                service.generate_draft(
                    turn_id=args.turn_id,
                    client=_client(args),
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                )
            )
            return 0
        if args.command == "approve":
            _print(service.approve_reply(turn_id=args.turn_id, approved_reply=args.reply, reviewer=args.reviewer))
            return 0
        if args.command == "reject":
            service.reject_reply(turn_id=args.turn_id, reviewer=args.reviewer, reason=args.reason)
            _print({"status": "rejected", "turn_id": args.turn_id})
            return 0
        if args.command == "memory-prompt":
            _print(service.memory_prompt(args.turn_id))
            return 0
        if args.command == "generate-memory":
            _print(
                service.generate_memory_proposal(
                    turn_id=args.turn_id,
                    client=_client(args),
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                )
            )
            return 0
        if args.command == "record-memory-output":
            _print(service.record_memory_output(turn_id=args.turn_id, raw_output=args.raw_output))
            return 0
        if args.command == "apply-memory":
            service.apply_memory_proposal(attempt_id=args.attempt_id, applied_by=args.reviewer)
            _print({"status": "applied", "attempt_id": args.attempt_id})
            return 0
        if args.command == "handoff":
            _print(service.executor_handoff(args.turn_id))
            return 0
        if args.command == "record-delivery":
            _print(
                service.record_delivery_confirmation(
                    turn_id=args.turn_id,
                    delivery_key=args.delivery_key,
                    delivered_at=args.delivered_at,
                )
            )
            return 0
        if args.command == "export-approved":
            output_directory = _safe_inside_system(args.output_dir, default=DATA_ROOT / "exports")
            _print(export_approved_examples(service.repository, output_directory))
            return 0
        if args.command == "list-quarantine":
            _print(service.repository.list_quarantined_observations())
            return 0
        raise CliError("unknown command")
    finally:
        service.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CliError, RepositoryError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

