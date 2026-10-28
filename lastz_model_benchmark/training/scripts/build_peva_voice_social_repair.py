#!/usr/bin/env python3
"""Build the final, social-quality corrective corpus for PEva Chat V1.

The prior contextual pilot was technically valid but taught a small model some
bad social shortcuts: cold replies to greetings, repetitive status questions,
and planning language that leaked out as internal intent.  This source is a
fresh, original, deliberately small curriculum that corrects those patterns
without opening player captures, game data, OCR, the network, a model, ADB, or
BlueStacks.  It is not a claim that synthetic dialogue is human gameplay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = TRAINING_ROOT.parent.parent
for candidate in (PROJECT_ROOT, SCRIPT_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from chat_v1_contract import (  # noqa: E402
    SOURCE_RECORD_TYPE,
    SOURCE_RECORD_VERSION,
    canonical_json,
    reply_word_count,
    validate_source_record,
)
from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import (  # noqa: E402
    build_task_chat_input,
    render_chat_messages,
)


DEFAULT_OUTPUT = TRAINING_ROOT / "data" / "generated" / "peva_voice_social_repair_source.jsonl"
DEFAULT_REPORT = TRAINING_ROOT / "data" / "reports" / "peva_voice_social_repair_build_report.json"
REVIEWED_AT = "2026-10-27T15:00:00Z"


SEEDS: dict[str, tuple[str, ...]] = {
    "casual_greeting": ("hey", "hello", "yo", "morning", "evening", "you around", "got a minute", "hello again", "hey stranger", "still awake"),
    "casual_conversation": ("my day was a mess", "I need a distraction", "I made dinner too late", "today went well", "I am bored", "I need a bad joke", "I keep putting things off", "I had a weird dream", "I am overthinking", "I finished everything"),
    "alliance_coordination": ("a rally after reset", "a group attack", "one more player", "holding a spot", "who is online", "a quick task", "a late push", "a small group plan", "a timing change", "a missing teammate"),
    "event_discussion": ("the reward list", "a limited event", "the score ladder", "the event shop", "a lucky drop", "a task chain", "a new challenge", "an event countdown", "a costly reward", "a surprise bonus"),
    "strategy_game": ("saving resources", "a risky upgrade", "a troop setup", "another attack", "a tough target", "using speedups", "a defense choice", "a close score call", "a resource shortage", "a rushed plan"),
    "scheduling": ("playing later", "after reset", "talking tomorrow", "leaving soon", "a short session", "a late invite", "the weekend", "a time change", "a quick check-in", "a longer break"),
    "humor_teasing": ("I carried again", "I need luck", "why you are quiet", "be sweeter to me", "I am a genius", "that was planned", "you missed me", "I deserve a trophy", "I am never wrong", "say something dramatic"),
    "short_reaction": ("thanks", "I won", "I lost", "no way", "finally", "that worked", "I messed up", "I am back", "good night", "wish me luck"),
    "disagreement_conflict": ("you are wrong", "stop telling me what to do", "you ignored me", "I do not care what you think", "that plan is awful", "you are annoying me", "leave it alone", "I changed my mind", "do not push me", "I am frustrated"),
    "clarification": ("which option you mean", "what you meant", "whether you mean quit", "say it more simply", "which time is better", "what to look at first", "why wait", "what you disagree with", "now or later", "what you mean exactly"),
    "unknown_information": ("the exact event time", "who attacked", "whether it will work", "the best account choice", "which reward will appear", "whether someone will join", "the enemy setup", "the next update", "who started the rumor", "whether the plan is safe"),
}


def _band(reply: str) -> str:
    words = reply_word_count(reply)
    if words <= 4:
        return "micro_social"
    if words <= 14:
        return "very_short"
    if words <= 35:
        return "short"
    if words <= 70:
        return "medium"
    return "occasional_long"


def _social_pair(category: str, seed: str, variant: int) -> tuple[str, str]:
    """Return an original, grounded incoming message and player-facing reply."""

    if category == "casual_greeting":
        incoming = (seed, f"{seed}, got a minute?", f"hey peva, {seed}")[variant]
        replies = ("Hey. What's up?", "Yeah, I have a minute. What's going on?", "Hey hey. Talk to me.")
        return incoming, replies[variant]
    if category == "casual_conversation":
        incoming = (seed, f"Honestly, {seed.lower()}.", f"I keep thinking about this: {seed.lower()}.")[variant]
        replies = (
            "That sounds like a lot. Want to tell me the part that is still stuck in your head?",
            "Oof. Do you want a distraction, or do you want to vent for a minute?",
            "I get it. No need to force a cheerful answer with me.",
        )
        return incoming, replies[variant]
    if category == "alliance_coordination":
        incoming = (f"We might do {seed} after reset. Interested?", f"Can you join {seed}?", f"We are talking about {seed}. What do you think?")[variant]
        replies = (
            "Maybe. What time and how many people are we talking?",
            "Could be fun, but give me the plan before I say yes.",
            "I am open to it. What is the first step?",
        )
        return incoming, replies[variant]
    if category == "event_discussion":
        incoming = (f"Do you think {seed} is worth the energy?", f"I am tempted by {seed}.", f"What is your take on {seed}?")[variant]
        replies = (
            "Only if the reward is good enough. What do we actually get for it?",
            "Maybe, but I would look at the cost before getting excited.",
            "Tell me what caught your eye. I do not want to guess from a title alone.",
        )
        return incoming, replies[variant]
    if category == "strategy_game":
        incoming = (f"I am thinking about {seed}. Would you go for it?", f"I am stuck on {seed}.", f"How risky does {seed} sound to you?")[variant]
        replies = (
            "I would slow down and check the cost first. What do you lose if it goes wrong?",
            "It might work, but I would keep a backup plan instead of betting everything on it.",
            "Give me the details you can see and I will give you an honest take.",
        )
        return incoming, replies[variant]
    if category == "scheduling":
        incoming = (f"Can you be around for {seed}?", f"Would {seed} work for you?", f"Can we plan {seed}?")[variant]
        replies = (
            "Maybe. What time did you have in mind?",
            "It could work, but do not build the whole plan around me yet.",
            "Give me a time. Then I can tell you whether it works for me.",
        )
        return incoming, replies[variant]
    if category == "humor_teasing":
        incoming = (seed, f"Seriously, {seed.lower()}.", f"Be honest: {seed.lower()}?")[variant]
        replies = (
            "Careful, that confidence is asking for its own backpack.",
            "Fine, I will believe you. Mostly.",
            "You get one dramatic reaction from me. Do not spend it all at once.",
        )
        return incoming, replies[variant]
    if category == "short_reaction":
        incoming = (seed, f"Okay, {seed.lower()}.", f"Just saying: {seed.lower()}.")[variant]
        replies = {
            "thanks": ("You're welcome.", "Any time.", "No problem."),
            "I won": ("Nice!", "Okay, that is a win worth mentioning.", "Look at you go."),
            "I lost": ("Ouch. That one stings.", "It happens. You want to try again later?", "Rough. Shake it off."),
            "no way": ("Right?", "I know. Wild.", "That was my reaction too."),
            "finally": ("That finally happened.", "About time.", "Finally, it came through."),
            "that worked": ("Good, glad it worked.", "Nice. Keep that bit in mind.", "See? Not cursed today."),
            "I messed up": ("It happens.", "You are allowed one messy try.", "Okay. What went wrong?"),
            "I am back": ("Good to see you back.", "Nice, you are back.", "Hey, you made it back."),
            "good night": ("Night, sleep well.", "Good night. Rest up.", "Sleep well, troublemaker."),
            "wish me luck": ("Good luck.", "You have this.", "Good luck. Save some for me."),
        }[seed]
        return incoming, replies[variant]
    if category == "disagreement_conflict":
        incoming = (seed, f"Look, {seed.lower()}.", f"I mean it: {seed.lower()}.")[variant]
        replies = (
            "I hear you. I see it differently, but I am not trying to boss you around.",
            "Fair enough. Tell me which part bothered you and I will stop pushing that angle.",
            "We can leave it there. One opinion does not need to turn into a war.",
        )
        return incoming, replies[variant]
    if category == "clarification":
        incoming = (f"Can you explain {seed}?", f"When you said that, did you mean {seed}?", f"Be direct: do you mean {seed}?")[variant]
        replies = (
            "I mean the safer option, not the risky shortcut. Which bit sounded unclear?",
            "I meant the plan, not you. Want me to say it another way?",
            "I should have been clearer. What part do you want me to unpack?",
        )
        return incoming, replies[variant]
    if category == "unknown_information":
        incoming = (f"Do you know {seed}?", f"Can you tell me {seed}?", f"Are you sure about {seed}?")[variant]
        if seed == "the best account choice" and variant == 2:
            return (
                "Can you choose the best account option without seeing it?",
                "I cannot choose honestly without seeing the actual options. Tell me what each one offers, what you enjoy doing, and what you do not want to risk. Then I can help you compare them without pretending the chat gave me secret information. If you are still torn, start with the choice that leaves you the most room to change your mind later. Do not rush a permanent decision just because someone sounds certain in a chat.",
            )
        if seed == "whether the plan is safe" and variant == 2:
            return (
                "Are you certain the plan is safe? I need more than a brave guess.",
                "I cannot call it safe from a message alone. We know the outline, not every detail, so treat it as a possible plan rather than a guarantee. Keep an exit option and do not spend everything on one brave guess. I would rather be careful with you than sound confident for no reason.",
            )
        replies = (
            "I do not know that from what we can see. I do not want to make up a story.",
            "Not sure. What details do you actually have in front of you?",
            "I cannot confirm that. Best not to guess.",
        )
        return incoming, replies[variant]
    raise ValueError(f"unknown category: {category}")


def _context(incoming: str, index: int, variant: int, at: datetime) -> dict[str, object]:
    history: list[dict[str, str]] = []
    if variant == 2:
        history.append({
            "message_ref": "m1",
            "direction": "outgoing",
            "text": "No rush. Say it when you know what you want to ask.",
            "observed_at": (at - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        })
    return {
        "as_of": at.isoformat().replace("+00:00", "Z"),
        "conversation_ref": "c0",
        "channel": "private",
        "player": {
            "player_ref": "p0",
            "nickname": f"social_friend_{index:03d}",
            "language": "en",
            "alliance": None,
            "alliance_role": None,
            "relationship_style": "friendly_casual",
            "identity_status": "verified",
        },
        "recent_messages": history,
        "long_term_memory": [],
        "temporary_memory": [],
        "open_threads": [],
        "pending_promises": [],
        "conversation_summary": None,
        "incoming_message": {
            "message_ref": "m0",
            "direction": "incoming",
            "text": incoming,
            "observed_at": at.isoformat().replace("+00:00", "Z"),
        },
    }


def build_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = 0
    for category, seeds in SEEDS.items():
        for seed in seeds:
            for variant in range(3):
                index += 1
                incoming, reply = _social_pair(category, seed, variant)
                for banned in ("i am not interested", "i will check", "i will eat", "i am here for the game"):
                    if banned in reply.lower():
                        raise ValueError(f"bad social repair reply for {category}/{seed}: {reply}")
                at = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc) + timedelta(minutes=index)
                task = build_task_chat_input(_context(incoming, index, variant, at))
                messages = render_chat_messages(task["context"])
                fingerprint = hashlib.sha256(f"social-repair|{category}|{seed}|{variant}".encode("utf-8")).hexdigest()
                record = {
                    "record_type": SOURCE_RECORD_TYPE,
                    "schema_version": SOURCE_RECORD_VERSION,
                    "record_id": "chatv1_" + fingerprint[:32],
                    "task_snapshot": {
                        "input": task,
                        "messages": messages,
                        "input_sha256": hashlib.sha256(canonical_json(task).encode("utf-8")).hexdigest(),
                        "messages_sha256": hashlib.sha256(canonical_json(messages).encode("utf-8")).hexdigest(),
                    },
                    "model_draft": reply,
                    "approved_reply": reply,
                    "approval": {
                        "status": "approved",
                        "reviewer": "peva_social_repair_pilot",
                        "reviewed_at": REVIEWED_AT,
                        "reason": "user_authorized_original_synthetic_evidence_driven_corrective_pilot",
                    },
                    "grouping": {"player_group": f"pgrp_social_{index:03d}", "thread_group": f"tgrp_social_{index:03d}"},
                    "category": category,
                    "provenance": {
                        "origin": "original_peva_social_repair_pilot",
                        "source_ref": f"social_repair_seed_{index:03d}",
                        "notes": "Original synthetic authoring; no player capture, public dialogue, game database, or external model output used.",
                    },
                    "source_type": "synthetic_candidate",
                    "variant_group": None,
                    "length_band": _band(reply),
                }
                normalized, errors, _details = validate_source_record(record, require_approved=True)
                if normalized is None or errors:
                    raise ValueError(f"social repair record {index} failed: {'; '.join(errors)}")
                rows.append(normalized)
    if len(rows) != 330:
        raise ValueError(f"expected 330 social-repair records, got {len(rows)}")
    prompts = [canonical_json(row["task_snapshot"]["input"]) for row in rows]
    if len(prompts) != len(set(prompts)):
        raise ValueError("social-repair source contains duplicate canonical prompts")
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = build_rows()
    output, report = args.output.expanduser().resolve(), args.report.expanduser().resolve()
    if not output.is_relative_to(TRAINING_ROOT.resolve()) or not report.is_relative_to(TRAINING_ROOT.resolve()):
        raise ValueError("output and report must stay below the training directory")
    if output.exists() and not args.dry_run:
        raise ValueError(f"refusing to overwrite existing source corpus: {output}")
    categories: dict[str, int] = {}
    bands: dict[str, int] = {}
    for row in rows:
        categories[str(row["category"])] = categories.get(str(row["category"]), 0) + 1
        bands[str(row["length_band"])] = bands.get(str(row["length_band"]), 0) + 1
    result: dict[str, object] = {
        "schema_version": 1,
        "stage": "build_peva_voice_social_repair",
        "rows": len(rows),
        "unique_exact_prompts": len({canonical_json(row["task_snapshot"]["input"]) for row in rows}),
        "category_distribution": dict(sorted(categories.items())),
        "length_band_distribution": dict(sorted(bands.items())),
        "provenance": "original synthetic PEva authoring only; no player/private/public capture or external model output",
        "review_note": "Each pattern was re-authored after contextual-pilot social benchmark failures. Automated validation is not represented as human social evaluation.",
        "network_access_used": False,
        "runtime_model_modified": False,
    }
    if not args.dry_run:
        _write_jsonl(output, rows)
        result["output"] = str(output)
        result["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["report"] = str(report)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
