"""Build review-filtered private-chat training datasets; never train a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from trening.agency_dataset_policy import load_cleanup_manifest, positive_real_eligibility
from trening.feedback_store import FeedbackRating, FeedbackStore
from config.runtime_config import DATA_DIR


RAW_DIR = DATA_DIR / "training" / "raw"
PROCESSED_DIR = DATA_DIR / "training" / "processed"


@dataclass(slots=True)
class TrainingSample:
    sample_id: str
    player_uid: str
    channel: str
    input_message: str
    recent_context: list[str]
    intent: str
    decision: str
    command_type: str | None
    command_status: str | None
    expected_reply: str
    quality: str
    source: str
    room_id: str = ""
    reply_guard_triggered: bool = False
    reply_rewritten: bool = False
    reply_rewrite_source: str = "original"
    guard_reason: str | None = None
    training_priority: int = 3

    def chatml(self) -> dict[str, Any]:
        context = "\n".join(self.recent_context)
        user = self.input_message if not context else f"Recent context:\n{context}\n\nMessage:\n{self.input_message}"
        return {
            "messages": [
                {"role": "system", "content": "You are PEva, a concise natural Last Z private-chat player. Never claim an unconfirmed game action."},
                {"role": "user", "content": user},
                {"role": "assistant", "content": self.expected_reply},
            ],
            "metadata": asdict(self),
        }


class DatasetBuilder:
    def __init__(
        self,
        *,
        raw_dir: str | Path = RAW_DIR,
        processed_dir: str | Path = PROCESSED_DIR,
        feedback_store: FeedbackStore | None = None,
    ) -> None:
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.feedback_store = feedback_store

    def build(self) -> list[TrainingSample]:
        records = list(self._read_raw_records())
        manifest = load_cleanup_manifest()
        keys = {str(record.get("message_key") or "") for record in records if record.get("message_key")}
        owned_store = self.feedback_store is None
        store = self.feedback_store or FeedbackStore()
        try:
            feedback = store.by_message_keys(keys)
        finally:
            if owned_store:
                store.close()
        samples: list[TrainingSample] = []
        seen: set[str] = set()
        for record in records:
            sample = self._to_sample(
                record,
                feedback.get(str(record.get("message_key") or "")),
                manifest=manifest,
            )
            if sample is None:
                continue
            fingerprint = f"{sample.room_id}|{sample.input_message}|{sample.expected_reply}"
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            samples.append(sample)
        return sorted(samples, key=lambda item: (-item.training_priority, item.sample_id))

    def export(self, samples: Iterable[TrainingSample], *, split: bool = False) -> dict[str, int]:
        all_samples = list(samples)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self._write_jsonl(self.processed_dir / "peva_private_v1.jsonl", (sample.chatml() for sample in all_samples))
        self._write_jsonl(self.processed_dir / "peva_decision_train.jsonl", (self._decision_row(sample) for sample in all_samples))
        counts = {"all": len(all_samples)}
        if split:
            train, validation = self._split_by_room(all_samples)
            self._write_jsonl(self.processed_dir / "peva_reply_train.jsonl", (sample.chatml() for sample in train))
            self._write_jsonl(self.processed_dir / "peva_reply_validation.jsonl", (sample.chatml() for sample in validation))
            counts.update(train=len(train), validation=len(validation))
        return counts

    def _read_raw_records(self) -> Iterable[dict[str, Any]]:
        if not self.raw_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.raw_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, Mapping):
                    records.append(dict(item))
        return records

    def _to_sample(
        self,
        record: Mapping[str, Any],
        feedback: Any | None,
        *,
        manifest: Mapping[str, Any] | None = None,
    ) -> TrainingSample | None:
        if str(record.get("channel") or "") != "custom":
            return None
        if str(record.get("decision") or "").upper() != "REPLY":
            return None
        message = _clean(record.get("input_message"))
        reply = _clean(record.get("reply"))
        if not message or not reply or _technical(message) or _technical(reply):
            return None
        context = record.get("recent_context")
        context_list = [str(item) for item in context] if isinstance(context, list) else []
        rewrite_source = str(record.get("reply_rewrite_source") or "original")
        quality = ""
        training_priority = 0
        if feedback is not None:
            if feedback.rating == FeedbackRating.BAD:
                return None
            if feedback.rating == FeedbackRating.EDITED and feedback.corrected_reply:
                reply = feedback.corrected_reply
                quality = "EDITED"
                training_priority = 4
            elif feedback.rating == FeedbackRating.GOOD:
                quality = "GOOD"
                training_priority = 5
        policy_record = dict(record)
        policy_record["reply"] = reply
        # Positive real data requires explicit human review.  A safe runtime
        # rewrite is evidence, not a gold label on its own.
        if not positive_real_eligibility(policy_record, feedback, manifest=manifest).eligible:
            return None
        if _unconfirmed_action_claim(reply, command_status=record.get("command_status")):
            return None
        key = str(record.get("message_key") or hashlib.sha256(f"{message}|{reply}".encode()).hexdigest()[:16])
        return TrainingSample(
            sample_id=key, player_uid=str(record.get("player_uid") or ""),
            channel="custom", input_message=message, recent_context=context_list,
            intent=str(record.get("intent") or "UNKNOWN"),
            decision="REPLY", command_type=_optional(record.get("command_type")),
            command_status=_optional(record.get("command_status")), expected_reply=reply,
            quality=quality, source="raw", room_id=str(record.get("room_id") or ""),
            reply_guard_triggered=bool(record.get("reply_guard_triggered")),
            reply_rewritten=bool(record.get("reply_rewritten")),
            reply_rewrite_source=rewrite_source,
            guard_reason=_optional(record.get("guard_reason")),
            training_priority=training_priority,
        )

    @staticmethod
    def _split_by_room(samples: list[TrainingSample]) -> tuple[list[TrainingSample], list[TrainingSample]]:
        train: list[TrainingSample] = []
        validation: list[TrainingSample] = []
        for sample in samples:
            group = sample.room_id or sample.player_uid or sample.sample_id
            bucket = int(hashlib.sha256(group.encode("utf-8")).hexdigest(), 16) % 10
            (validation if bucket == 0 else train).append(sample)
        return train, validation

    @staticmethod
    def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _decision_row(sample: TrainingSample) -> dict[str, Any]:
        return {
            "sample_id": sample.sample_id,
            "input_message": sample.input_message,
            "recent_context": sample.recent_context,
            "intent": sample.intent,
            "decision": sample.decision,
            "command_type": sample.command_type,
            "command_status": sample.command_status,
            "quality": sample.quality,
            "reply_guard_triggered": sample.reply_guard_triggered,
            "reply_rewritten": sample.reply_rewritten,
            "reply_rewrite_source": sample.reply_rewrite_source,
            "training_priority": sample.training_priority,
        }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _technical(text: str) -> bool:
    return text.startswith("[") or "Traceback" in text or text.startswith("{\"")


_ACTION_CLAIM = re.compile(
    r"\b(?:done|reinforced|sent\s+(?:troops|reinforcements)|(?:i(?:'ll| will)\s+)?send(?:ing)?\s+(?:troops|reinforcements)|joined(?:\s+the\s+rally)?|teleported|attack(?:ed)?\s+(?:him|them|now)|(?:i(?:'ll| will)|i can|i(?:'m| am) going to)\s+(?:reinforce|join|teleport|attack|send|come))\b",
    re.I,
)
_UNCONFIRMED_ACCEPTANCE = re.compile(
    r"^\s*(?:sure thing|on it|will do|got it|sounds good|okay|ok|i can(?: do that)?|i(?:'ll| will) do it)\s*[!.]*\s*$",
    re.I,
)


def _unconfirmed_action_claim(reply: str, *, command_status: Any) -> bool:
    status = str(command_status or "").upper()
    # A bare acknowledgement is too ambiguous to be useful training material
    # even when an older raw row lost its command-status metadata.
    if _UNCONFIRMED_ACCEPTANCE.fullmatch(reply):
        return True
    return status != "EXECUTED" and bool(_ACTION_CLAIM.search(reply))


def _optional(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PEva private-chat datasets; does not train.")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--split", action="store_true")
    args = parser.parse_args()
    if not args.build:
        parser.print_help()
        return
    builder = DatasetBuilder()
    result = builder.export(builder.build(), split=args.split)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
