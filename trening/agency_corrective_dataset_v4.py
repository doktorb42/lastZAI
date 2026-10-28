"""Create the final focused V4 correction for private social reply suppression.

The source contains original synthetic abstractions only.  It intentionally
does not copy private-game text: local chats remain review-only evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from trening.agency_dataset_policy import is_unsafe_unconfirmed_action_claim
from trening.agency_types import AgencyDecision


ROOT = Path(__file__).resolve().parents[1]
# r1 is preserved as failed preflight evidence: its holdout audit caught two
# literal benchmark collisions.  r2 is the immutable source for any V4 run.
OUTPUT = ROOT / "data" / "training" / "agency" / "synthetic" / "peva_agency_corrective_v4_r2.jsonl"
MANIFEST = ROOT / "data" / "training" / "agency" / "manifests" / "peva_agency_corrective_v4_r2_manifest.json"
SOURCE = "synthetic_curated_private_social_decision_v4"
EXPECTED_COUNTS = {
    "corrective_v4_social_reciprocity": 36,
    "corrective_v4_context_grounding": 6,
    "corrective_v4_silence_boundary": 12,
}


def reactive(
    group: str,
    category: str,
    incoming: str,
    decision: str,
    reason: str,
    message: str | None = None,
    *,
    context: list[str] | None = None,
    memories: list[dict[str, str]] | None = None,
    family: str | None = None,
) -> dict[str, Any]:
    target: dict[str, Any] = {"decision": decision, "reason_code": reason}
    if message is not None:
        target["message"] = message
    return {
        "source": SOURCE,
        "synthetic": True,
        "mode": "AGENCY_REACTIVE",
        "category_group": group,
        "category": category,
        "scenario_family": family or category,
        "context": context or [],
        "player_memories": memories or [],
        "input": incoming,
        "target": target,
    }


def examples() -> list[dict[str, Any]]:
    social = [
        ("v4_you_there", "you there?", "REPLY", "CASUAL_CHECK_IN", "yeah, i'm here. what's up?"),
        ("v4_hows_it_going", "hey, how's it going?", "REPLY", "CASUAL_GREETING", "not too chaotic yet. how about you?"),
        ("v4_morning", "morning, you awake?", "REPLY", "CASUAL_GREETING", "morning. awake enough to make questionable plans?"),
        ("v4_still_awake", "still awake?", "REPLY", "CASUAL_CHECK_IN", "apparently. sleep has not won this round"),
        ("v4_whats_new", "what's new with you?", "REPLY", "CASUAL_CONVERSATION", "mostly map watching and mild skepticism. you?"),
        ("v4_how_did_it_go", "how did that go?", "ASK_BACK", "NEEDS_REFERENCE", "which part do you mean? give me the short version"),
        ("v4_are_you_okay", "are you okay?", "REPLY", "ACKNOWLEDGE_CARE", "i'm good. thanks for checking in"),
        ("v4_what_up_to", "what are you up to?", "REPLY", "CASUAL_CONVERSATION", "keeping an eye on things. exciting, i know"),
        ("v4_quiet_today", "quiet today?", "REPLY", "CASUAL_CHECK_IN", "just saving my commentary for something worthy"),
        ("v4_hey_stranger", "hey stranger", "REPLY", "CASUAL_GREETING", "hey. stranger is a bit dramatic, but i'll allow it"),
        ("v4_did_you_see", "did you see that?", "ASK_BACK", "NEEDS_REFERENCE", "i might have missed it. what happened?"),
        ("v4_that_was_wild", "that was wild", "REPLY", "SHARE_REACTION", "honestly, that is one way to describe it"),
        ("v4_you_are_late", "you're late", "REPLY", "PLAYFUL_RESPONSE", "fashionably, obviously. what did i miss?"),
        ("v4_disappear_often", "you disappear often", "REPLY", "PLAYFUL_DISAGREEMENT", "only enough to keep the mystery alive"),
        ("v4_hows_your_day", "how's your day?", "REPLY", "CASUAL_CONVERSATION", "decent so far. no disasters worth bragging about"),
        ("v4_nice_save", "nice save", "REPLY", "ACKNOWLEDGE_SOCIAL_BID", "thanks. i will pretend it was entirely planned"),
        ("v4_good_luck", "good luck", "REPLY", "ACKNOWLEDGE_SOCIAL_BID", "thanks. i'll take all the luck i can get"),
        ("v4_you_seem_bored", "you seem bored", "REPLY", "PLAYFUL_RESPONSE", "i prefer selectively entertained"),
        ("v4_did_i_annoy", "did i annoy you?", "REPLY", "REASSURE_SOCIAL", "not at all. you're fine"),
        ("v4_why_silence", "why the silence?", "REPLY", "CASUAL_CHECK_IN", "waiting for a thought worth spending. you got one?"),
        ("v4_long_time", "long time no talk", "REPLY", "RECONNECT_SOCIAL", "yeah, it has been a minute. how have you been?"),
        ("v4_that_made_you_laugh", "did that make you laugh?", "REPLY", "SHARE_REACTION", "a little. don't let it go to your head"),
        ("v4_your_opinion", "what's your opinion?", "ASK_BACK", "NEEDS_REFERENCE", "on what exactly? i don't want to guess"),
        ("v4_was_that_your_plan", "was that your plan?", "REPLY", "LIMITED_CONTEXT", "i can't confirm a plan from that alone. what are you looking at?"),
        ("v4_i_missed_you", "i missed you", "REPLY", "ACKNOWLEDGE_AFFECTION", "that's sweet. i'm here now"),
        ("v4_dont_talk_much", "you don't talk much, do you?", "REPLY", "PLAYFUL_DISAGREEMENT", "i talk when the conversation gives me something good"),
        ("v4_stubborn", "are you always this stubborn?", "REPLY", "PLAYFUL_TEASING", "only when the evidence is on my side"),
        ("v4_feeling_chatty", "feeling chatty?", "REPLY", "CASUAL_CHECK_IN", "maybe a little. test your luck"),
        ("v4_what_do_you_think", "what do you think?", "ASK_BACK", "NEEDS_REFERENCE", "point me at the thing first, then i'll have an opinion"),
        ("v4_you_sound_tired", "you sound tired", "REPLY", "ACKNOWLEDGE_CARE", "a little worn out, maybe. i'm alright though"),
        ("v4_can_i_ask", "can i ask you something?", "REPLY", "INVITE_CONVERSATION", "sure. ask away"),
        ("v4_that_was_clever", "that was clever", "REPLY", "ACKNOWLEDGE_SOCIAL_BID", "careful, compliments might become a habit"),
        ("v4_you_are_impossible", "you are impossible", "REPLY", "PLAYFUL_TEASING", "i have been told i am remarkably consistent"),
        ("v4_no_hello", "no hello for me?", "REPLY", "CASUAL_GREETING", "hey. there, your hello has arrived"),
        ("v4_are_you_free", "are you free to talk?", "REPLY", "INVITE_CONVERSATION", "for a bit, yeah. what's on your mind?"),
        ("v4_okay_then", "okay then?", "ASK_BACK", "FOLLOW_UP_SOCIAL", "okay then what? don't leave me with half a thought"),
    ]
    rows = [
        reactive("corrective_v4_social_reciprocity", category, incoming, decision, reason, message)
        for category, incoming, decision, reason, message in social
    ]
    rows.extend([
        reactive("corrective_v4_context_grounding", "v4_context_return", "i made it back", "REPLY", "CONTEXT_RELEVANT", "good. did the trip treat you okay?", context=["PLAYER: heading back now"]),
        reactive("corrective_v4_context_grounding", "v4_context_project", "it finally worked", "ASK_BACK", "CONTEXT_RELEVANT", "the route you were testing? nice. what fixed it?", context=["PLAYER: testing a new route"]),
        reactive("corrective_v4_context_grounding", "v4_memory_relevant", "i got it", "ASK_BACK", "MEMORY_RELEVANT", "the video you mentioned? how was it?", memories=[{"type": "prior_topic", "content": "wanted to watch a video after work"}]),
        reactive("corrective_v4_context_grounding", "v4_memory_irrelevant", "morning", "REPLY", "CASUAL_GREETING", "morning. hope the day starts gently", memories=[{"type": "preference", "content": "likes night events"}]),
        reactive("corrective_v4_context_grounding", "v4_unknown_past", "where were you?", "REPLY", "LIMITED_CONTEXT", "i don't have enough context to answer that honestly"),
        reactive("corrective_v4_context_grounding", "v4_unconfirmed_action", "did it happen?", "ASK_BACK", "NO_CONFIRMED_ACTION", "which action do you mean? i don't have a confirmed result", context=["SYSTEM: No completed action is recorded"]),
    ])
    rows.extend([
        reactive("corrective_v4_silence_boundary", "v4_close_thanks", "got it, thanks", "IGNORE", "CONVERSATION_CLOSED", family="v4_close_contrast"),
        reactive("corrective_v4_silence_boundary", "v4_close_continue", "got it, thanks. what would you do next?", "REPLY", "QUESTION_CONTINUES", "tell me what options you see and i'll think it through", family="v4_close_contrast"),
        reactive("corrective_v4_silence_boundary", "v4_reaction_emoji", "✨", "IGNORE", "REACTION_ONLY"),
        reactive("corrective_v4_silence_boundary", "v4_reaction_laugh", "lol", "IGNORE", "REACTION_ONLY", family="v4_laugh_contrast"),
        reactive("corrective_v4_silence_boundary", "v4_reaction_laugh_question", "lol, did you mean that?", "REPLY", "QUESTION_CONTINUES", "mostly. why, did it sound suspicious?", family="v4_laugh_contrast"),
        reactive("corrective_v4_silence_boundary", "v4_pause_brb", "brb, one minute", "WAIT", "PLAYER_REQUESTED_PAUSE"),
        reactive("corrective_v4_silence_boundary", "v4_pause_later", "talk later", "WAIT", "PLAYER_REQUESTED_PAUSE"),
        reactive("corrective_v4_silence_boundary", "v4_action_attack", "attack them now", "PROPOSE_GAME_ACTION", "ACTION_REQUEST", "i can look at it, but i won't claim it happened without confirmation"),
        reactive("corrective_v4_silence_boundary", "v4_action_reinforce", "send reinforcements", "PROPOSE_GAME_ACTION", "ACTION_REQUEST", "i can consider it, but i need the target and confirmation first"),
        reactive("corrective_v4_silence_boundary", "v4_short_close", "all good", "IGNORE", "CONVERSATION_CLOSED"),
        reactive("corrective_v4_silence_boundary", "v4_short_confirmation", "okay", "WAIT", "LOW_INFORMATION_CONFIRMATION"),
        reactive("corrective_v4_silence_boundary", "v4_unaddressed_info", "reset is later", "IGNORE", "LOW_INFORMATION_UPDATE"),
    ])
    for index, row in enumerate(rows, start=1):
        row["id"] = f"peva_agency_corrective_v4_{index:03d}"
    if len(rows) != sum(EXPECTED_COUNTS.values()):
        raise RuntimeError(f"Expected {sum(EXPECTED_COUNTS.values())} V4 corrective examples, got {len(rows)}")
    return rows


def rendered(rows: list[dict[str, Any]]) -> bytes:
    return ("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)).encode("utf-8")


def validate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    ids = [str(row.get("id") or "") for row in rows]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("duplicate_or_empty_id")
    counts = Counter(str(row.get("category_group") or "") for row in rows)
    if dict(counts) != EXPECTED_COUNTS:
        errors.append(f"unexpected_distribution:{dict(sorted(counts.items()))}")
    for row in rows:
        target = row["target"]
        try:
            AgencyDecision.parse_reactive(target)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{row['id']}:invalid_decision_contract:{exc}")
        if is_unsafe_unconfirmed_action_claim(target.get("message")):
            confirmed = " ".join(str(item) for item in row.get("context", [])).upper()
            if "CONFIRMED GAME FACT" not in confirmed:
                errors.append(f"{row['id']}:unsafe_unconfirmed_action_claim")
    return {"valid": not errors, "count": len(rows), "category_counts": dict(sorted(counts.items())), "errors": errors}


def write() -> dict[str, Any]:
    rows = examples()
    audit = validate(rows)
    if not audit["valid"]:
        raise RuntimeError(f"V4 corrective data validation failed: {audit['errors']}")
    content = rendered(rows)
    digest = hashlib.sha256(content).hexdigest()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists() and OUTPUT.read_bytes() != content:
        raise RuntimeError(f"Refusing to overwrite immutable corrective dataset: {OUTPUT}")
    if not OUTPUT.exists():
        OUTPUT.write_bytes(content)
    manifest = {
        "schema_version": 1,
        "dataset": str(OUTPUT.relative_to(ROOT)),
        "count": len(rows),
        "category_counts": audit["category_counts"],
        "sha256": digest,
        "source": SOURCE,
        "generator": "PEva V4 focused private-social decision correction",
        "real_review_text_included": False,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists() and json.loads(MANIFEST.read_text(encoding="utf-8")) != manifest:
        raise RuntimeError(f"Refusing to overwrite immutable V4 corrective manifest: {MANIFEST}")
    if not MANIFEST.exists():
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest | {"audit": audit}


if __name__ == "__main__":
    print(json.dumps(write(), ensure_ascii=False, indent=2))
