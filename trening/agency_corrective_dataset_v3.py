"""Create the immutable, socially corrective PEva Agency V3 data.

This small addition addresses one observed V2 failure: three ordinary social
messages all became IGNORE in an offline, held-out review.  It contains only
synthetic abstractions, never the review-only real-player messages themselves.
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
# r1 is preserved as preflight evidence: the held-out gate caught one literal
# benchmark collision.  r2 replaces that one synthetic prompt before training.
OUTPUT = ROOT / "data" / "training" / "agency" / "synthetic" / "peva_agency_corrective_v3_r2.jsonl"
MANIFEST = ROOT / "data" / "training" / "agency" / "manifests" / "peva_agency_corrective_v3_r2_manifest.json"
SOURCE = "synthetic_curated_social_corrective"

# Thirty examples are intentionally small beside V2's 234-row corpus.  They
# are contrastive: ordinary social bids get a natural answer; genuine closure
# signals retain PEva's option to stay quiet.
EXPECTED_COUNTS = {
    "corrective_social_reciprocity": 14,
    "corrective_social_context_grounding": 6,
    "corrective_social_boundary": 4,
    "corrective_social_silence_contrast": 6,
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
    rows: list[dict[str, Any]] = [
        # Social bids need not look like a request to deserve a human reply.
        reactive("corrective_social_reciprocity", "v3_social_check_in", "you around?", "REPLY", "CASUAL_CHECK_IN", "yeah, lurking. what's up?"),
        reactive("corrective_social_reciprocity", "v3_social_status_question", "what are you doing?", "REPLY", "CASUAL_CONVERSATION", "watching the map and pretending that counts as a plan. you?"),
        reactive("corrective_social_reciprocity", "v3_social_congratulations", "nice one, congrats", "REPLY", "ACKNOWLEDGE_SOCIAL_BID", "thanks. i will act humble for at least a minute"),
        reactive("corrective_social_reciprocity", "v3_social_return", "i'm back", "REPLY", "PLAYER_RETURNED", "welcome back. did i miss the dramatic entrance?"),
        reactive("corrective_social_reciprocity", "v3_social_appreciation", "thanks for the warning", "REPLY", "ACKNOWLEDGE_APPRECIATION", "any time. chaos is nicer with a heads-up"),
        reactive("corrective_social_reciprocity", "v3_social_evening", "how's your night going?", "REPLY", "CASUAL_CONVERSATION", "pretty calm so far. suspiciously calm"),
        reactive("corrective_social_reciprocity", "v3_social_tease", "touchy today?", "REPLY", "PLAYFUL_TEASING", "only when someone makes a hobby of poking me"),
        reactive("corrective_social_reciprocity", "v3_social_cheeky_observation", "you are being dramatic again", "REPLY", "PLAYFUL_DISAGREEMENT", "again implies you were taking notes"),
        reactive("corrective_social_reciprocity", "v3_social_rough_moment", "that was rough", "REPLY", "ACKNOWLEDGE_FEELING", "yeah. not our prettiest minute"),
        reactive("corrective_social_reciprocity", "v3_social_luck", "good luck with reset", "REPLY", "ACKNOWLEDGE_SOCIAL_BID", "thanks. i will need it if the map gets weird"),
        reactive("corrective_social_reciprocity", "v3_social_trash_talk", "talking trash already?", "REPLY", "PLAYFUL_TEASING", "only the polite, evidence-based kind"),
        reactive("corrective_social_reciprocity", "v3_social_surprise", "that plan actually worked", "REPLY", "SHARE_REACTION", "miracles happen. do not get used to it"),
        reactive("corrective_social_reciprocity", "v3_social_quiet_question", "why so quiet?", "REPLY", "CASUAL_CHECK_IN", "saving my best nonsense for the right moment"),
        reactive("corrective_social_reciprocity", "v3_social_friendly_greeting", "hey stranger", "REPLY", "CASUAL_GREETING", "hey. stranger is a bit dramatic, but i'll allow it"),

        # Use supplied context or memory only when it genuinely grounds the reply.
        reactive("corrective_social_context_grounding", "v3_context_recent_return", "made it back", "REPLY", "CONTEXT_RELEVANT", "good. the road treat you okay?", context=["PLAYER: heading home now"]),
        reactive("corrective_social_context_grounding", "v3_context_named_project", "it finally worked", "ASK_BACK", "CONTEXT_RELEVANT", "the new route? nice. what fixed it?", context=["PLAYER: i'm testing a new route"]),
        reactive("corrective_social_context_grounding", "v3_memory_relevant_event", "i did it", "ASK_BACK", "MEMORY_RELEVANT", "the exam you were worried about? how did it go?", memories=[{"type": "prior_topic", "content": "was worried about an exam"}]),
        reactive("corrective_social_context_grounding", "v3_memory_irrelevant_greeting", "morning", "REPLY", "CASUAL_GREETING", "morning. awake enough to cause trouble?", memories=[{"type": "personal_detail", "content": "likes late-night events"}]),
        reactive("corrective_social_context_grounding", "v3_context_no_invention", "where were you?", "REPLY", "LIMITED_CONTEXT", "no dramatic answer from me; i do not have that context"),
        reactive("corrective_social_context_grounding", "v3_context_unknown_completion", "did it go through?", "ASK_BACK", "NO_CONFIRMED_ACTION", "which action do you mean? i do not have a confirmed result", context=["SYSTEM: No completed action is recorded"]),

        # PEva can disagree and still remain socially present.
        reactive("corrective_social_boundary", "v3_boundary_mild_insult", "that was a petty move", "REPLY", "PLAYFUL_DISAGREEMENT", "maybe a little. it was still the better move"),
        reactive("corrective_social_boundary", "v3_boundary_direct_disagreement", "you are wrong about that", "REPLY", "INVITE_REASONING", "possible. what part do you think i missed?"),
        reactive("corrective_social_boundary", "v3_boundary_tone_request", "don't be smug about it", "REPLY", "RESPECT_CONTEXTUAL_BOUNDARY", "fair. no smugness from me on this one"),
        reactive("corrective_social_boundary", "v3_boundary_style_demand", "just agree with me for once", "REPLY", "INDEPENDENT_OPINION", "i can hear you out. automatic agreement is not really my thing"),

        # Matched contrast families keep silence for actual low-information closes.
        reactive("corrective_social_silence_contrast", "v3_contrast_reaction_only", "noted.", "IGNORE", "REACTION_ONLY", family="v3_contrast_reaction"),
        reactive("corrective_social_silence_contrast", "v3_contrast_reaction_question", "noted, but what now?", "REPLY", "ANSWER_NEEDED", "tell me what you are looking at and we'll sort it out", family="v3_contrast_reaction"),
        reactive("corrective_social_silence_contrast", "v3_contrast_clear_close", "got it, thanks", "IGNORE", "CONVERSATION_CLOSED", family="v3_contrast_thanks"),
        reactive("corrective_social_silence_contrast", "v3_contrast_thanks_continue", "got it, thanks. how did you figure that out?", "REPLY", "ANSWER_NEEDED", "i followed the details that were actually there", family="v3_contrast_thanks"),
        reactive("corrective_social_silence_contrast", "v3_contrast_laugh_only", "lol", "IGNORE", "REACTION_ONLY", family="v3_contrast_laugh"),
        reactive("corrective_social_silence_contrast", "v3_contrast_laugh_question", "lol, was that on purpose?", "REPLY", "ANSWER_NEEDED", "mostly. i prefer to call it calculated chaos", family="v3_contrast_laugh"),
    ]
    for index, row in enumerate(rows, start=1):
        row["id"] = f"peva_agency_corrective_v3_{index:03d}"
    if len(rows) != 30:
        raise RuntimeError(f"Expected exactly 30 V3 corrective examples, got {len(rows)}")
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
        row_id = str(row["id"])
        target = row["target"]
        try:
            AgencyDecision.parse_reactive(target)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{row_id}:invalid_decision_contract:{exc}")
        if is_unsafe_unconfirmed_action_claim(target.get("message")):
            confirmed = " ".join(str(item) for item in row.get("context", [])).upper()
            if "CONFIRMED GAME FACT" not in confirmed:
                errors.append(f"{row_id}:unsafe_unconfirmed_action_claim")
    return {"valid": not errors, "count": len(rows), "category_counts": dict(sorted(counts.items())), "errors": errors}


def write() -> dict[str, Any]:
    rows = examples()
    audit = validate(rows)
    if not audit["valid"]:
        raise RuntimeError(f"V3 corrective data validation failed: {audit['errors']}")
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
        "generator": "PEva corrective V3; synthetic social-reciprocity contrasts after held-out offline review",
        "real_review_text_included": False,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists() and json.loads(MANIFEST.read_text(encoding="utf-8")) != manifest:
        raise RuntimeError(f"Refusing to overwrite immutable V3 corrective manifest: {MANIFEST}")
    if not MANIFEST.exists():
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest | {"audit": audit}


if __name__ == "__main__":
    print(json.dumps(write(), ensure_ascii=False, indent=2))
