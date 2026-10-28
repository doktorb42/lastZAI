"""Terminal review workflow for PEva humanization material.

It is intentionally offline: reviewing data never starts capture, navigation,
the LLM, or any send path.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from trening.agency_store import AgencyStore
from charakter.humanization_store import BAD_REPLY_REASONS, HumanizationStore
from config.runtime_config import CONVERSATION_DB_PATH, DATA_DIR
from mozg.conversation_store import ConversationStore


def render_player_review(store: HumanizationStore, conversation: ConversationStore, player_uid: str, *, reply: dict | None = None) -> str:
    profile = store.get_profile(player_uid)
    style = store.get_style(player_uid)
    memories = store.get_memories(player_uid, limit=12)
    pending_memories = store.list_memory_candidates(player_uid=player_uid)
    pending_style = store.list_style_candidates(player_uid=player_uid)
    lines = [
        "=" * 60,
        f"Player UID: {player_uid}",
        f"Relationship: {profile.relationship}",
        f"Familiarity: {profile.familiarity:.2f}",
        "", "STYLE ARCHIVE (not Agency decision authority)",
        f"softness: {style.softness:.2f}",
        f"playfulness: {style.playfulness:.2f}",
        f"teasing: {style.teasing:.2f}",
        f"directness: {style.directness:.2f}",
        f"formality: {style.formality:.2f}",
        f"emoji: {style.emoji_level}",
        f"reply length: {style.reply_length}",
        f"tags: {', '.join(style.tags) or '-'}",
        "", "ACTIVE MEMORY",
    ]
    if memories:
        lines.extend(f"[{item.memory_id}] {item.memory_type}: {item.content}" for item in memories)
    else:
        lines.append("(none)")
    lines.extend(["", "PENDING MEMORY"])
    if pending_memories:
        lines.extend(f"[{item['candidate_id']}] {item['memory_type']}: {item['content']}" for item in pending_memories)
    else:
        lines.append("(none)")
    if pending_style:
        lines.append("PENDING STYLE")
        lines.extend(f"[{item['candidate_id']}] {item['change_json']}" for item in pending_style)
    lines.extend(["", "RECENT CONTEXT"])
    recent = conversation.get_messages(player_uid=player_uid, limit=8)
    if recent:
        lines.extend(f"{'PEva' if item.direction == 'outgoing' else 'Player'}: {item.text}" for item in recent)
    else:
        lines.append("(none)")
    if reply:
        lines.extend(["", "REPLY TO REVIEW", str(reply.get("reply_text") or "")])
    lines.extend(["", "[G] Good  [B] Bad  [E] Edit reply  [M] Memory", "[F] Failure reason  [P] Proactive follow-up  [S] Skip  [Q] Quit", "=" * 60])
    return "\n".join(lines)


def interactive_review(store: HumanizationStore, conversation: ConversationStore, review_id: str,
                       *, input_func: Callable[[str], str] = input, output: Callable[[str], None] = print) -> None:
    review = store.get_reply_review(review_id)
    if review is None:
        raise KeyError(f"Unknown reply review: {review_id}")
    uid = str(review["player_uid"])
    while True:
        output(render_player_review(store, conversation, uid, reply=review))
        choice = input_func("Review: ").strip().upper()
        if choice == "Q": return
        if choice == "G":
            store.review_reply(review_id, status="GOOD")
            return
        if choice == "S":
            store.review_reply(review_id, status="SKIPPED")
            return
        if choice in {"B", "F"}:
            output("Reasons: " + ", ".join(sorted(BAD_REPLY_REASONS)))
            reason = input_func("Reason [OTHER]: ").strip().upper() or "OTHER"
            store.review_reply(review_id, status="BAD", bad_reason=reason)
            return
        if choice == "E":
            text = input_func("Edited reply: ").strip()
            if text:
                store.review_reply(review_id, status="EDITED", edited_reply=text)
                return
        if choice == "M":
            candidate = input_func("Memory candidate ID to approve: ").strip()
            if candidate: store.approve_memory_candidate(candidate)
        if choice == "P":
            answer = input_func("Would a follow-up be natural? [y/n]: ").strip().lower()
            suggestion = input_func("Optional future example: ").strip() if answer in {"y", "yes"} else None
            store.add_proactive_review(player_uid=uid, approved=answer in {"y", "yes"}, suggestion=suggestion)


def render_agency_review(decision: dict[str, object]) -> str:
    return "\n".join([
        "=" * 60,
        f"Agency decision: {decision.get('decision_id')}",
        f"Player: {decision.get('player_uid') or '-'} room={decision.get('room_id') or '-'}",
        f"Incoming: {decision.get('incoming_text') or '-'}",
        f"Decision: {decision.get('decision')} ({decision.get('reason_code')})",
        f"Message: {decision.get('message') or '-'}",
        "", "Review: PEva-like? natural? context used correctly? identity kept? was this decision right?",
        "[G] Good  [B] Bad  [E] Edit decision/message  [S] Skip  [Q] Quit",
        "=" * 60,
    ])


def interactive_agency_review(store: AgencyStore, decision_id: str, *, input_func: Callable[[str], str] = input, output: Callable[[str], None] = print) -> None:
    decision = store.get_decision(decision_id)
    if decision is None:
        raise KeyError(f"Unknown Agency decision: {decision_id}")
    while True:
        output(render_agency_review(decision))
        choice = input_func("Review: ").strip().upper()
        if choice == "Q":
            return
        if choice == "G":
            store.add_feedback(decision_id, rating="GOOD")
            return
        if choice == "B":
            notes = input_func("Why was it wrong? ").strip() or None
            store.add_feedback(decision_id, rating="BAD", notes=notes)
            return
        if choice == "S":
            store.add_feedback(decision_id, rating="SKIP")
            return
        if choice == "E":
            corrected_decision = input_func("Correct decision (optional): ").strip().upper() or None
            corrected_message = input_func("Correct message (optional): ").strip() or None
            store.add_feedback(decision_id, rating="EDITED", corrected_decision=corrected_decision, corrected_message=corrected_message)
            return


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline PEva humanization review")
    parser.add_argument("--list", action="store_true", help="List pending reply reviews.")
    parser.add_argument("--summary", action="store_true", help="Show queue and profile counts.")
    parser.add_argument("--player", help="Show a player profile, memory and recent conversation.")
    parser.add_argument("--review", help="Interactively review one reply review ID.")
    parser.add_argument("--agency-list", action="store_true", help="List recent Agency decisions.")
    parser.add_argument("--agency-review", help="Interactively review one Agency decision ID.")
    parser.add_argument("--db", default=str(DATA_DIR / "databases" / "humanization.db"), help="Humanization SQLite path.")
    parser.add_argument("--conversation-db", default=str(CONVERSATION_DB_PATH), help="Conversation SQLite path.")
    args = parser.parse_args()
    with HumanizationStore(Path(args.db)) as store, ConversationStore(Path(args.conversation_db)) as conversation:
        if args.summary:
            print(f"Pending replies: {len(store.list_reply_reviews(status='PENDING', limit=10000))}")
            print(f"Pending memories: {len(store.list_memory_candidates(status='PENDING'))}")
            print(f"Pending styles: {len(store.list_style_candidates(status='PENDING'))}")
        if args.list:
            for item in store.list_reply_reviews(status="PENDING"):
                print(f"{item['review_id']} uid={item['player_uid']} reply={item['reply_text']!r}")
        if args.player:
            print(render_player_review(store, conversation, args.player))
        if args.review:
            interactive_review(store, conversation, args.review)
        if args.agency_list or args.agency_review:
            with AgencyStore(Path(args.db)) as agency:
                if args.agency_list:
                    for item in agency.recent_decisions(limit=100):
                        print(f"{item['decision_id']} uid={item.get('player_uid') or '-'} decision={item['decision']} message={item.get('message')!r}")
                if args.agency_review:
                    interactive_agency_review(agency, args.agency_review)
        if not any((args.summary, args.list, args.player, args.review, args.agency_list, args.agency_review)):
            parser.print_help()


if __name__ == "__main__":
    main()
