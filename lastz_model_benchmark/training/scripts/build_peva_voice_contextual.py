#!/usr/bin/env python3
"""Build a privacy-safe corrective PEva voice corpus with unique contexts.

Unlike the first voice pilot, this source has exactly one reply target for
each rendered prompt.  Its purpose is to avoid teaching a small model that one
incoming message has many interchangeable completions.  All dialogue below is
original synthetic authoring; it never opens game capture, SQLite, OCR, a
model, the network, ADB, or BlueStacks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = TRAINING_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

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


DEFAULT_OUTPUT = TRAINING_ROOT / "data" / "generated" / "peva_voice_contextual_source.jsonl"
DEFAULT_REPORT = TRAINING_ROOT / "data" / "reports" / "peva_voice_contextual_build_report.json"
REVIEWED_AT = "2026-10-27T14:00:00Z"


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


# Ten distinct, grounded subjects per category.  Each is expressed in three
# different conversational situations below; no exact TASK_CHAT prompt is
# reused and no artificial alternative-target group is needed.
SEEDS: dict[str, tuple[str, ...]] = {
    "casual_greeting": (
        "hey", "yo", "hello", "morning", "evening", "you around", "got a minute",
        "hello again", "hey stranger", "still awake",
    ),
    "casual_conversation": (
        "I finally finished everything", "my day was a mess", "I need a distraction",
        "I cannot stop overthinking", "I made dinner too late", "today went surprisingly well",
        "I am bored", "I need a bad joke", "I keep putting things off", "I had a weird dream",
    ),
    "alliance_coordination": (
        "a rally after reset", "a spot in the group attack", "waiting for one more player",
        "holding a position for a bit", "checking who is online", "a quick alliance task",
        "a late-night event push", "a smaller group plan", "a timing change", "a missing teammate",
    ),
    "event_discussion": (
        "the reward list", "a limited event", "the score ladder", "the event shop", "a lucky drop",
        "a boring task chain", "a new challenge", "an event countdown", "a costly reward", "a surprise bonus",
    ),
    "strategy_game": (
        "saving resources", "a risky upgrade", "choosing a troop setup", "a repeated attack",
        "a target that looks tough", "spending speedups", "a defense question", "a close score choice",
        "a resource shortage", "a plan that might be too rushed",
    ),
    "scheduling": (
        "playing later", "being online after reset", "talking tomorrow", "leaving soon",
        "a short session", "a late invite", "a weekend plan", "a time change", "a quick check-in", "a long break",
    ),
    "humor_teasing": (
        "I carried again", "I need luck", "why are you quiet", "be sweeter to me", "I am a genius",
        "that was totally planned", "you missed me", "I deserve a trophy", "I am never wrong", "say something dramatic",
    ),
    "short_reaction": (
        "thanks", "I won", "I lost", "no way", "finally", "that worked", "I messed up",
        "I am back", "good night", "wish me luck",
    ),
    "disagreement_conflict": (
        "you are wrong", "stop telling me what to do", "you ignored me", "I do not care what you think",
        "that plan is awful", "you are being annoying", "leave it alone", "I changed my mind", "do not push me", "I am frustrated",
    ),
    "clarification": (
        "which option do you mean", "what did you mean by that", "are you telling me to quit",
        "say it more simply", "which timing is better", "what should I check first", "why wait",
        "what are you disagreeing with", "do you mean now or later", "can you be specific",
    ),
    "unknown_information": (
        "the exact event time", "who attacked us", "whether this will work", "the best account choice",
        "what reward will appear", "whether someone will join", "the exact enemy setup", "the next update",
        "who started the rumor", "whether the plan is safe",
    ),
}


def _phrases(category: str, seed: str, variant: int) -> tuple[str, str, list[dict[str, str]], list[dict[str, object]]]:
    """Return one visible situation, one answer, and optional grounding.

    Every reply only refers to a detail present in the current text, small
    visible history, or explicit preference memory.  This is intentionally
    plain English: PEva may be playful, but she should not become a catchphrase
    machine or a customer-support script.
    """

    direct = [
        (f"hey peva, {seed}", "Hey. I am around. What's up?"),
        (f"quick one: {seed}", "Okay, tell me the short version."),
        (f"{seed} - what do you think?", "I have an opinion, but give me one more detail first."),
    ]
    if category == "casual_greeting":
        replies = ("Hey. What's up?", "Yo, I am here. What did you need?", "Hi. Talk to me.")
        incoming = (seed, f"{seed}, you around?", f"{seed} - got a minute?")[variant]
        return incoming, replies[variant], [], []
    if category == "casual_conversation":
        replies = (
            f"That sounds like a whole thing. Want to talk about {seed.lower()}?",
            "Okay, no pressure. Give me the interesting part when you feel like it.",
            "I hear you. We can keep it light or pick it apart, your call.",
        )
        incoming = (seed, f"Honestly, {seed.lower()}.", f"I keep thinking about this: {seed.lower()}.")[variant]
        history = [] if variant != 2 else [{"message_ref": "m1", "direction": "outgoing", "text": "I am listening, no rush.", "observed_at": "2026-10-27T14:00:00Z"}]
        return incoming, replies[variant], history, []
    if category == "alliance_coordination":
        replies = (
            f"Maybe. What is the timing for {seed}?",
            f"I can consider {seed}, but I need the plan before I promise.",
            f"Give me the details on {seed}; I do not want to guess and then vanish.",
        )
        incoming = (f"can you help with {seed}?", f"we are planning {seed}; are you in?", f"do you have time for {seed} later?")[variant]
        return incoming, replies[variant], [], []
    if category == "event_discussion":
        replies = (
            f"What about {seed} looks worth it to you?",
            f"I would check the cost before I get excited about {seed}.",
            f"Maybe. I am not spending everything on {seed} without a reason.",
        )
        incoming = (f"what do you think about {seed}?", f"is {seed} worth doing?", f"I am tempted by {seed}.")[variant]
        return incoming, replies[variant], [], []
    if category == "strategy_game":
        replies = (
            f"Before deciding on {seed}, what are you trying to get next?",
            f"I would not rush {seed} without checking what it costs you afterward.",
            f"For {seed}, I would keep a backup plan instead of pretending it is certain.",
        )
        incoming = (f"should I go for {seed}?", f"I am stuck on {seed}.", f"would you gamble on {seed}?")[variant]
        return incoming, replies[variant], [], []
    if category == "scheduling":
        replies = (
            f"Maybe. What time were you thinking for {seed}?",
            f"{seed.capitalize()} could work, but do not make me guess the plan.",
            f"I cannot promise {seed} yet. Ping me with a time and I will say straight.",
        )
        incoming = (f"want to talk about {seed}?", f"are you free for {seed}?", f"can we plan {seed}?")[variant]
        return incoming, replies[variant], [], []
    if category == "humor_teasing":
        player_subject = seed.lower().replace("i am", "you are").replace("i need", "you need")
        replies = (
            f"{player_subject.capitalize()}? Careful, your ego is asking for its own backpack.",
            f"Fine, I will believe you about {player_subject}. Mostly.",
            "You get one dramatic reaction from me. Do not spend it all at once.",
        )
        incoming = (seed, f"seriously, {seed.lower()}.", f"be honest: {seed.lower()}?")[variant]
        return incoming, replies[variant], [], []
    if category == "short_reaction":
        replies = (
            "Nice.", "Oof, that is rough.", "No problem.", "I know, right?", "Finally, nice.",
            "Good, that worked.", "It happens. Shake it off.", "Hey, welcome back.",
            "Night. Rest up.", "You have got this.",
        )
        # The three phrasings carry distinct social turns but stay concise.
        incoming = (seed, f"okay, {seed.lower()}", f"just saying: {seed.lower()}")[variant]
        return incoming, replies[SEEDS[category].index(seed)], [], []
    if category == "disagreement_conflict":
        replies = (
            "Fair. I was giving an opinion, not an order.",
            "Okay. Tell me what part bothered you and I will listen.",
            "We can disagree without turning this into a fight.",
        )
        incoming = (seed, f"look, {seed.lower()}.", f"I mean it: {seed.lower()}.")[variant]
        return incoming, replies[variant], [], []
    if category == "clarification":
        details = ("the safer option", "the timing", "the next step")
        replies = (
            f"I mean {details[0]}, not the risky shortcut.",
            f"I am talking about {details[1]}, not you personally.",
            f"I mean {details[2]}. I should have said it more clearly.",
        )
        incoming = (f"{seed}?", f"when you said that, {seed}?", f"be direct: {seed}?")[variant]
        return incoming, replies[variant], [], []
    if category == "unknown_information":
        if seed == "the best account choice" and variant == 2:
            return (
                "can you tell me the best account choice without seeing it?",
                "I cannot honestly choose for an account I cannot see. Tell me the actual options, what you enjoy most, and what you do not want to risk. Then I can give you a real opinion instead of dressing up a guess. If the choice can wait, I would start with the option that is easiest to undo and see what changes before spending anything important.",
                [],
                [],
            )
        if seed == "whether the plan is safe" and variant == 2:
            return (
                "are you certain the plan is safe? I need more than a brave guess.",
                "No, I cannot promise that from a chat. We know the outline, not every hidden detail, so I would treat it as a calculated try rather than a guarantee. Check what we can see, keep an exit option, and do not spend everything just because the plan sounds confident. If a part still feels unclear, ask that question before moving. I would rather adjust a plan with you than pretend certainty and leave you with the consequences.",
                [],
                [],
            )
        replies = (
            f"I do not know {seed}. I would rather say that than make something up.",
            f"I cannot confirm {seed} from this chat. What do you actually see?",
            f"No blind guess from me about {seed}. Check the visible details first.",
        )
        incoming = (f"do you know {seed}?", f"can you tell me {seed}?", f"are you sure about {seed}?")[variant]
        return incoming, replies[variant], [], []
    return direct[variant][0], direct[variant][1], [], []


def _context(incoming: str, index: int, history: list[dict[str, str]], memories: list[dict[str, object]], at: datetime) -> dict[str, object]:
    stamped_history: list[dict[str, str]] = []
    for item in history:
        copy = dict(item)
        copy["observed_at"] = (at - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        stamped_history.append(copy)
    return {
        "as_of": at.isoformat().replace("+00:00", "Z"),
        "conversation_ref": "c0",
        "channel": "private",
        "player": {"player_ref": "p0", "nickname": f"context_friend_{index:03d}", "language": "en", "alliance": None, "alliance_role": None, "relationship_style": "friendly_casual", "identity_status": "verified"},
        "recent_messages": stamped_history,
        "long_term_memory": memories,
        "temporary_memory": [],
        "open_threads": [],
        "pending_promises": [],
        "conversation_summary": None,
        "incoming_message": {"message_ref": "m0", "direction": "incoming", "text": incoming, "observed_at": at.isoformat().replace("+00:00", "Z")},
    }


def build_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = 0
    for category, seeds in SEEDS.items():
        for seed in seeds:
            for variant in range(3):
                index += 1
                incoming, reply, history, memories = _phrases(category, seed, variant)
                at = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=index)
                task = build_task_chat_input(_context(incoming, index, history, memories, at))
                messages = render_chat_messages(task["context"])
                fingerprint = hashlib.sha256(f"contextual|{category}|{seed}|{variant}".encode("utf-8")).hexdigest()
                record = {
                    "record_type": SOURCE_RECORD_TYPE,
                    "schema_version": SOURCE_RECORD_VERSION,
                    "record_id": "chatv1_" + fingerprint[:32],
                    "task_snapshot": {"input": task, "messages": messages, "input_sha256": hashlib.sha256(canonical_json(task).encode("utf-8")).hexdigest(), "messages_sha256": hashlib.sha256(canonical_json(messages).encode("utf-8")).hexdigest()},
                    "model_draft": reply,
                    "approved_reply": reply,
                    "approval": {"status": "approved", "reviewer": "peva_contextual_pilot", "reviewed_at": REVIEWED_AT, "reason": "user_authorized_original_synthetic_corrective_pilot"},
                    "grouping": {"player_group": f"pgrp_context_{index:03d}", "thread_group": f"tgrp_context_{index:03d}"},
                    "category": category,
                    "provenance": {"origin": "original_peva_contextual_pilot", "source_ref": f"contextual_seed_{index:03d}", "notes": "Original synthetic authoring; no player capture, public dialogue, game database, or external model output used."},
                    "source_type": "synthetic_candidate",
                    "variant_group": None,
                    "length_band": _band(reply),
                }
                normalized, errors, _details = validate_source_record(record, require_approved=True)
                if normalized is None or errors:
                    raise ValueError(f"contextual record {index} failed: {'; '.join(errors)}")
                rows.append(normalized)
    if len(rows) != 330:
        raise ValueError(f"expected 330 contextual records, got {len(rows)}")
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
    result: dict[str, object] = {"schema_version": 1, "stage": "build_peva_voice_contextual", "rows": len(rows), "unique_exact_prompts": len({canonical_json(row["task_snapshot"]["input"]) for row in rows}), "category_distribution": dict(sorted(categories.items())), "length_band_distribution": dict(sorted(bands.items())), "provenance": "original synthetic PEva authoring only; no player/private/public capture or external model output", "review_note": "Technically approved under the user's controlled-pilot authorisation. Automated validation is not represented as human social evaluation.", "network_access_used": False, "runtime_model_modified": False}
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
