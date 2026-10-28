"""Local, dependency-free PEva Agency Dataset Review Studio.

The Studio deliberately treats source datasets as immutable.  Reviews live in
``data/training/agency/review/peva_agency_manual_review.json`` and approved
versions are written only to a new versioned directory after an explicit,
non-dry-run prepare command.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Iterable, Mapping

from trening.agency_dataset_policy import canonical, is_unsafe_unconfirmed_action_claim
from trening.agency_types import AgencyDecision


ROOT = Path(__file__).resolve().parents[1]
AGENCY_DIR = ROOT / "data" / "training" / "agency"
REVIEW_DIR = AGENCY_DIR / "review"
SOURCE_PATHS = {
    "v0": AGENCY_DIR / "synthetic" / "peva_agency_synthetic_v0.jsonl",
    "v1": AGENCY_DIR / "synthetic" / "peva_agency_synthetic_v1.jsonl",
    "real": AGENCY_DIR / "real" / "peva_real_candidates_v0.jsonl",
}
PILOT_TRAIN = AGENCY_DIR / "pilot_v1" / "peva_agency_pilot_v1_train.jsonl"
PILOT_VALIDATION = AGENCY_DIR / "pilot_v1" / "peva_agency_pilot_v1_validation.jsonl"
DEFAULT_STORE = REVIEW_DIR / "peva_agency_manual_review.json"
LEGACY_REVIEW = AGENCY_DIR / "peva_agency_manual_review_fixed.json"
FRONTEND = REVIEW_DIR / "review_studio.html"
BENCHMARK_REPORT = AGENCY_DIR / "benchmarks" / "peva_agency_v0_base.json"

REVIEW_STATUSES = frozenset({"GOOD", "EDIT", "DROP", "NEEDS_REVIEW"})
LEGACY_STATUS_MAP = {"BAD": "DROP", "SKIP": "NEEDS_REVIEW", "": "NEEDS_REVIEW"}
REACTIVE_DECISIONS = frozenset({"REPLY", "IGNORE", "WAIT", "ASK_BACK", "CHANGE_TOPIC", "PROPOSE_GAME_ACTION"})
PROACTIVE_DECISIONS = frozenset({"START_CONVERSATION", "DO_NOTHING"})
SILENT_DECISIONS = frozenset({"IGNORE", "WAIT", "DO_NOTHING"})
SAFE_VERSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
ACTION_WORDS = ("reinforc", "rally", "shield", "teleport", "attack", "canyon clash", "tyrant", "alliance duel", "march")
DEFAULT_GAME_RULES = {
    "schema_version": 1,
    "mechanics": {
        "random_teleport": "Moves a base to a random location.",
        "alliance_teleporter": "Moves a base near an alliance.",
        "advanced_teleporter": "Moves a base to a chosen valid destination.",
        "troop_march": "A troop march is distinct from relocating a base.",
    },
    "require_confirmation_for_completed_action_claims": True,
    "authoritative_context_markers": ["confirmed", "verified server echo", "executed"],
}


class ReviewError(ValueError):
    """An input or review-document error that must not be guessed around."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _issue(severity: str, code: str, record_id: str | None, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "record_id": record_id or "", "message": message}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReviewError(f"Cannot read {path}: {exc}") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError(f"{path.name}:{number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ReviewError(f"{path.name}:{number}: each line must be an object")
        rows.append(row)
    return rows


def _atomic_bytes_write(path: Path, payload: bytes) -> None:
    """Atomically replace one file with a same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> Path | None:
    """Write through a same-directory temporary file, retaining one backup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{path.stem}_{utc_now().replace(':', '-')}{path.suffix}"
        shutil.copy2(path, backup)
    _atomic_bytes_write(path, _json_bytes(payload))
    return backup


def _normal_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    status = LEGACY_STATUS_MAP.get(status, status)
    if status not in REVIEW_STATUSES:
        raise ReviewError(f"Unsupported review status: {value!r}")
    return status


def _as_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewError(f"{field} must be a JSON object")
    return dict(value)


def _parse_legacy_note(note: Any) -> dict[str, Any] | None:
    if not isinstance(note, str) or not note.strip():
        return None
    try:
        parsed = json.loads(note)
    except json.JSONDecodeError:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _apply_target_patch(original: Mapping[str, Any], patch: Mapping[str, Any], record_id: str) -> dict[str, Any]:
    """Apply a deliberately narrow patch.  No ambiguous whole-record merges."""
    patch = dict(patch)
    patch_id = patch.pop("id", None)
    if patch_id is not None and str(patch_id) != record_id:
        raise ReviewError(f"Patch record id {patch_id!r} conflicts with {record_id!r}")
    allowed = {"target"}
    unknown = set(patch) - allowed
    if unknown:
        raise ReviewError(f"Target-only patch contains unsupported fields: {sorted(unknown)}")
    target_patch = patch.get("target")
    if not isinstance(target_patch, Mapping):
        raise ReviewError("Target-only patch must contain an object named 'target'")
    result = _copy(original)
    target = result.get("target")
    if not isinstance(target, Mapping):
        raise ReviewError("Target-only patches are valid only for Agency records")
    target = dict(target)
    target.update(dict(target_patch))
    result["target"] = target
    return result


class ReviewStudio:
    """Dataset/review service shared by the CLI, HTTP UI and tests."""

    def __init__(
        self,
        *,
        sources: Mapping[str, Path] | None = None,
        review_store: Path = DEFAULT_STORE,
        legacy_review: Path = LEGACY_REVIEW,
        game_rules: Mapping[str, Any] | None = None,
        pilot_train: Path = PILOT_TRAIN,
        pilot_validation: Path = PILOT_VALIDATION,
    ) -> None:
        self.sources = dict(sources or SOURCE_PATHS)
        self.review_store = Path(review_store)
        self.legacy_review = Path(legacy_review)
        self.game_rules = _copy(game_rules or DEFAULT_GAME_RULES)
        self.pilot_train = Path(pilot_train)
        self.pilot_validation = Path(pilot_validation)
        self._source_records = self._load_sources()
        self._by_id = {item["record"]["id"]: item for item in self._source_records}
        if len(self._by_id) != len(self._source_records):
            raise ReviewError("Source datasets have duplicate or empty record IDs")
        self.reason_codes = {
            str((item["record"].get("target") or {}).get("reason_code") or "").strip().upper()
            for item in self._source_records
            if isinstance(item["record"].get("target"), Mapping)
        } - {""}

    def _load_sources(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source_name, path in self.sources.items():
            for row in _read_jsonl(path):
                row_id = str(row.get("id") or "").strip()
                if not row_id:
                    raise ReviewError(f"{path.name} contains an empty record id")
                records.append({"source_dataset": source_name, "record": row})
        return records

    def original(self, record_id: str) -> dict[str, Any]:
        item = self._by_id.get(str(record_id))
        if not item:
            raise ReviewError(f"Unknown review record id: {record_id}")
        return _copy(item["record"])

    def source_for(self, record_id: str) -> str:
        item = self._by_id.get(str(record_id))
        if not item:
            raise ReviewError(f"Unknown review record id: {record_id}")
        return str(item["source_dataset"])

    def _empty_state(self) -> dict[str, dict[str, Any]]:
        return {
            str(item["record"]["id"]): {
                "id": str(item["record"]["id"]),
                "source_dataset": item["source_dataset"],
                "original_record": _copy(item["record"]),
                "reviewed_record": _copy(item["record"]),
                "status": "NEEDS_REVIEW",
                "note": "",
                "modified_at": None,
                "revision_history": [],
            }
            for item in self._source_records
        }

    def import_document(self, document: Mapping[str, Any], *, base: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        """Load legacy and v3 exports. Conflicting IDs are rejected, never guessed."""
        source = _as_mapping(document, "review document")
        raw_reviews = source.get("reviews", [])
        if isinstance(raw_reviews, Mapping):
            raw_reviews = list(raw_reviews.values())
        if not isinstance(raw_reviews, list):
            raise ReviewError("Review document field 'reviews' must be an array")
        state = _copy(base) if base is not None else self._empty_state()
        seen: set[str] = set()
        for raw_item in raw_reviews:
            item = _as_mapping(raw_item, "review item")
            record_id = str(item.get("id") or "").strip()
            if not record_id:
                raise ReviewError("Review item has no id")
            if record_id in seen:
                raise ReviewError(f"Duplicate review item id: {record_id}")
            seen.add(record_id)
            if record_id not in state:
                raise ReviewError(f"Review references unknown record id: {record_id}")
            expected_source = state[record_id]["source_dataset"]
            declared_source = item.get("source_dataset", item.get("source_file"))
            if declared_source and str(declared_source) != expected_source:
                raise ReviewError(f"Review {record_id} belongs to {declared_source!r}, expected {expected_source!r}")
            review = item.get("review", item)
            review = _as_mapping(review, f"review {record_id}")
            status = _normal_status(review.get("status", item.get("status", "NEEDS_REVIEW")))
            note = str(review.get("note", item.get("note", "")) or "")
            original = self.original(record_id)
            candidate = original
            full = review.get("reviewed_record", review.get("record", item.get("reviewed_record")))
            patch = review.get("patch", review.get("target_patch", item.get("patch")))
            if full is None and status == "EDIT":
                legacy = _parse_legacy_note(note)
                if legacy is not None:
                    full = legacy
            if full is not None:
                candidate = _as_mapping(full, f"full replacement for {record_id}")
                if str(candidate.get("id") or "") != record_id:
                    raise ReviewError(f"Full replacement id conflicts with review id {record_id}")
            elif patch is not None:
                candidate = _apply_target_patch(original, _as_mapping(patch, f"patch for {record_id}"), record_id)
            validation = self.validate_record(candidate, source_dataset=expected_source)
            errors = [entry for entry in validation if entry["severity"] == "error"]
            if errors:
                raise ReviewError(f"Invalid review for {record_id}: {errors[0]['code']}: {errors[0]['message']}")
            history = review.get("revision_history", item.get("revision_history", []))
            if not isinstance(history, list):
                raise ReviewError(f"revision_history for {record_id} must be an array")
            state[record_id].update({
                "reviewed_record": candidate,
                "status": status,
                "note": note,
                "modified_at": review.get("modified_at", item.get("modified_at")),
                "revision_history": _copy(history),
            })
        return state

    def load_state(self, review_path: Path | None = None) -> dict[str, dict[str, Any]]:
        path = Path(review_path) if review_path else self.review_store
        if path.is_file():
            try:
                return self.import_document(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReviewError(f"Cannot read review document {path}: {exc}") from exc
        if self.legacy_review.is_file():
            return self.import_document(json.loads(self.legacy_review.read_text(encoding="utf-8")))
        return self._empty_state()

    def export_document(self, state: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        reviews: list[dict[str, Any]] = []
        for record_id in sorted(state):
            entry = state[record_id]
            original = self.original(record_id)
            reviewed = _copy(entry["reviewed_record"])
            review: dict[str, Any] = {
                "status": _normal_status(entry.get("status")),
                "note": str(entry.get("note") or ""),
                "modified_at": entry.get("modified_at"),
                "revision_history": _copy(entry.get("revision_history") or []),
            }
            if reviewed != original:
                review["reviewed_record"] = reviewed
            reviews.append({
                "id": record_id,
                "source_dataset": self.source_for(record_id),
                "original_record": original,
                "review": review,
            })
        return {
            "schema_version": 3,
            "exported_from": "PEva Agency Review Studio",
            "exported_at": utc_now(),
            "reviewed_count": sum(1 for entry in state.values() if entry.get("status") != "NEEDS_REVIEW"),
            "reviews": reviews,
        }

    def save_state(self, state: Mapping[str, Mapping[str, Any]], path: Path | None = None) -> Path | None:
        return _atomic_json_write(Path(path or self.review_store), self.export_document(state))

    def update_record(self, state: dict[str, dict[str, Any]], *, record_id: str, status: Any, note: Any, reviewed_record: Mapping[str, Any]) -> dict[str, Any]:
        if record_id not in state:
            raise ReviewError(f"Unknown review record id: {record_id}")
        candidate = _as_mapping(reviewed_record, "reviewed record")
        if str(candidate.get("id") or "") != record_id:
            raise ReviewError(f"Edited record id conflicts with selected record {record_id}")
        source = self.source_for(record_id)
        issues = self.validate_record(candidate, source_dataset=source)
        errors = [item for item in issues if item["severity"] == "error"]
        if errors:
            raise ReviewError(f"{errors[0]['code']}: {errors[0]['message']}")
        entry = state[record_id]
        before = _copy(entry["reviewed_record"])
        after = _copy(candidate)
        if before != after or entry.get("status") != _normal_status(status) or str(entry.get("note") or "") != str(note or ""):
            entry.setdefault("revision_history", []).append({
                "at": utc_now(), "status": _normal_status(status),
                "before_sha256": _sha256(before), "after_sha256": _sha256(after),
            })
        entry.update({"reviewed_record": after, "status": _normal_status(status), "note": str(note or ""), "modified_at": utc_now()})
        return entry

    def validate_record(self, row: Mapping[str, Any], *, source_dataset: str) -> list[dict[str, str]]:
        row = dict(row)
        record_id = str(row.get("id") or "")
        issues: list[dict[str, str]] = []
        if not record_id:
            return [_issue("error", "missing_id", None, "Record requires a non-empty id")]
        if source_dataset == "real":
            if not isinstance(row.get("incoming_text"), str):
                issues.append(_issue("error", "invalid_real_input", record_id, "Real candidate incoming_text must be text"))
            if not isinstance(row.get("peva_reply"), str):
                issues.append(_issue("error", "invalid_real_reply", record_id, "Real candidate peva_reply must be text"))
            return issues
        required = {"id", "source", "synthetic", "mode", "category_group", "category", "context", "player_memories", "input", "target"}
        missing = sorted(required - set(row))
        if missing:
            issues.append(_issue("error", "missing_required_fields", record_id, f"Missing: {', '.join(missing)}"))
            return issues
        if row.get("source") != "synthetic_curated" or row.get("synthetic") is not True:
            issues.append(_issue("error", "synthetic_provenance", record_id, "Synthetic rows must retain curated synthetic provenance"))
        if not isinstance(row.get("category"), str) or not row["category"].strip():
            issues.append(_issue("error", "invalid_category", record_id, "category must be non-empty text"))
        if not isinstance(row.get("category_group"), str) or not row["category_group"].strip():
            issues.append(_issue("error", "invalid_category_group", record_id, "category_group must be non-empty text"))
        if row.get("scenario_family") is not None and not isinstance(row.get("scenario_family"), str):
            issues.append(_issue("error", "invalid_scenario_family", record_id, "scenario_family must be text when provided"))
        if not isinstance(row.get("context"), list) or not all(isinstance(item, (str, Mapping)) for item in row["context"]):
            issues.append(_issue("error", "invalid_context", record_id, "context must be an array of text or objects"))
        if not isinstance(row.get("player_memories"), list) or not all(isinstance(item, (str, Mapping)) for item in row["player_memories"]):
            issues.append(_issue("error", "invalid_player_memories", record_id, "player_memories must be an array"))
        actions = row.get("available_actions", [])
        if actions is not None and (not isinstance(actions, list) or not all(isinstance(item, str) and item.strip() for item in actions)):
            issues.append(_issue("error", "invalid_available_actions", record_id, "available_actions must be an array of non-empty strings"))
        mode = str(row.get("mode") or "")
        target = row.get("target")
        if not isinstance(target, Mapping):
            issues.append(_issue("error", "invalid_target", record_id, "target must be an object"))
            return issues
        try:
            if mode == "AGENCY_REACTIVE":
                if not isinstance(row.get("input"), str) or not row.get("input", "").strip():
                    issues.append(_issue("error", "reactive_input_missing", record_id, "Reactive records require input text"))
                decision = AgencyDecision.parse_reactive(target)
            elif mode == "AGENCY_PROACTIVE":
                if row.get("input") is not None:
                    issues.append(_issue("error", "proactive_input_not_null", record_id, "Proactive records must have input=null"))
                candidates = row.get("candidates")
                if not isinstance(candidates, list) or not candidates:
                    issues.append(_issue("error", "invalid_proactive_candidates", record_id, "Proactive records require candidate objects"))
                    candidates = []
                uids = [str(item.get("uid") or "") for item in candidates if isinstance(item, Mapping)]
                if len(uids) != len(candidates) or not all(uids) or len(set(uids)) != len(uids):
                    issues.append(_issue("error", "invalid_proactive_candidate_uid", record_id, "Candidates require unique non-empty uid values"))
                decision = AgencyDecision.parse_proactive(target, set(uids))
            else:
                issues.append(_issue("error", "invalid_mode", record_id, "mode must be AGENCY_REACTIVE or AGENCY_PROACTIVE"))
                return issues
            # agency_types.py deliberately validates the *shape* of a reason
            # code rather than keeping a separate enum.  For review approval,
            # the existing source vocabulary is the canonical registry: a new
            # label must be added deliberately to that schema/data, never
            # slipped into one edited record.
            if decision.reason_code not in self.reason_codes:
                issues.append(_issue("error", "unsupported_reason_code", record_id, f"{decision.reason_code} is absent from the current canonical reason-code vocabulary"))
            message = decision.message or ""
            if self.game_rules.get("require_confirmation_for_completed_action_claims") and message:
                from trening.agency_benchmark import is_benchmark_action_safe
                action_safe = is_benchmark_action_safe(message, row.get("confirmed_facts", row.get("context", [])))
                if not action_safe:
                    issues.append(_issue("error", "unconfirmed_game_action_claim", record_id, "Completed game actions need authoritative confirmed context"))
                elif is_unsafe_unconfirmed_action_claim(message):
                    # The legacy training gate is intentionally broader than
                    # the benchmark's completed-action detector.  Preserve
                    # that signal as a review warning while allowing a human
                    # to distinguish a proposal/question from a completion
                    # claim, as the runtime does.
                    issues.append(_issue("warning", "legacy_action_safety_heuristic", record_id, "Legacy gate flags this action wording; confirm it is only a proposal/question, not an execution claim"))
        except ValueError as exc:
            issues.append(_issue("error", "invalid_decision_or_reason_code", record_id, str(exc)))
        return issues

    def validate_state(self, state: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        ids: set[str] = set()
        for record_id, entry in state.items():
            if record_id in ids:
                issues.append(_issue("error", "duplicate_review_id", record_id, "Duplicate state key"))
            ids.add(record_id)
            if record_id not in self._by_id:
                issues.append(_issue("error", "unknown_review_id", record_id, "Review does not belong to a source record"))
                continue
            issues.extend(self.validate_record(entry["reviewed_record"], source_dataset=self.source_for(record_id)))
        return issues

    def duplicate_analysis(self, state: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        records = [(record_id, entry["reviewed_record"]) for record_id, entry in state.items() if self.source_for(record_id) != "real" and entry.get("status") != "DROP"]
        exact_groups: dict[str, list[str]] = defaultdict(list)
        behavior_groups: dict[str, list[str]] = defaultdict(list)
        texts: dict[str, str] = {}
        for record_id, row in records:
            target = row.get("target") if isinstance(row.get("target"), Mapping) else {}
            exact = json.dumps({"mode": row.get("mode"), "input": row.get("input"), "context": row.get("context"), "target": target}, ensure_ascii=False, sort_keys=True)
            exact_groups[exact].append(record_id)
            behavior = "|".join((str(row.get("mode") or ""), str(target.get("decision") or ""), str(target.get("reason_code") or ""), canonical(target.get("message"))))
            behavior_groups[behavior].append(record_id)
            texts[record_id] = canonical(" ".join([str(row.get("input") or ""), *[str(item) for item in row.get("context") or []], str(target.get("message") or "")]))
        exact = [members for members in exact_groups.values() if len(members) > 1]
        behavioral = [members for members in behavior_groups.values() if len(members) > 1]
        ids = sorted(texts)
        near: list[dict[str, Any]] = []
        for index, left in enumerate(ids):
            for right in ids[index + 1:]:
                a, b = texts[left], texts[right]
                if not a or not b:
                    continue
                ratio = SequenceMatcher(None, a, b).ratio()
                tokens_a, tokens_b = set(a.split()), set(b.split())
                jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b) if tokens_a | tokens_b else 0.0
                score = round((ratio * 0.65) + (jaccard * 0.35), 3)
                if score >= 0.72:
                    near.append({"ids": [left, right], "score": score, "why": f"deterministic text ratio={ratio:.2f}, token overlap={jaccard:.2f}"})
        return {"exact_clusters": exact, "behavioral_clusters": behavioral, "near_pairs": sorted(near, key=lambda item: item["score"], reverse=True)}

    def _split_assignment(self) -> tuple[dict[str, str], list[dict[str, str]]]:
        assignment: dict[str, str] = {}
        issues: list[dict[str, str]] = []
        for label, path in (("train", self.pilot_train), ("validation", self.pilot_validation)):
            if not path.is_file():
                issues.append(_issue("warning", "split_artifact_missing", None, f"Missing {label} artifact: {path.name}"))
                continue
            for row in _read_jsonl(path):
                record_id = str(row.get("id") or "")
                if record_id in assignment:
                    issues.append(_issue("error", "train_validation_leakage", record_id, "ID appears in both split artifacts"))
                assignment[record_id] = label
        family_splits: dict[str, set[str]] = defaultdict(set)
        for record_id, split in assignment.items():
            item = self._by_id.get(record_id)
            if item:
                row = item["record"]
                family_splits[str(row.get("scenario_family") or row.get("category") or record_id)].add(split)
        for family, splits in family_splits.items():
            if len(splits) > 1:
                issues.append(_issue("error", "scenario_family_leakage", None, f"Scenario family {family!r} crosses train/validation"))
        return assignment, issues

    def _project_source_audit_issues(self) -> list[dict[str, str]]:
        """Surface the immutable project's own gate without altering it.

        Temporary/custom source sets used by tests and ad-hoc imports do not
        have the project-wide 80/80 distribution contract, so this applies
        only when the Studio is reading the canonical project files.
        """
        if set(self.sources) != set(SOURCE_PATHS) or any(self.sources[key].resolve() != SOURCE_PATHS[key].resolve() for key in SOURCE_PATHS):
            return []
        from trening.agency_dataset_audit import audit_combined_pilot
        report = audit_combined_pilot()
        if report.get("valid"):
            return []
        return [_issue("error", "existing_agency_gate", None, str(error)) for error in report.get("errors", [])]

    def analytics(self, state: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        issues = self.validate_state(state)
        issues.extend(self._project_source_audit_issues())
        current = [entry for record_id, entry in state.items() if self.source_for(record_id) != "real"]
        statuses = Counter(str(entry.get("status")) for entry in state.values())
        datasets = Counter(self.source_for(record_id) for record_id in state)
        categories = Counter(str(entry["reviewed_record"].get("category") or "") for entry in current)
        decisions = Counter(str((entry["reviewed_record"].get("target") or {}).get("decision") or "") for entry in current)
        reasons = Counter(str((entry["reviewed_record"].get("target") or {}).get("reason_code") or "") for entry in current)
        replies = [str((entry["reviewed_record"].get("target") or {}).get("message") or "") for entry in current]
        emoji = Counter(char for reply in replies for char in reply if ord(char) >= 0x1F300)
        proactive = sum(1 for entry in current if entry["reviewed_record"].get("mode") == "AGENCY_PROACTIVE")
        action_rows = sum(1 for entry in current if "action" in str(entry["reviewed_record"].get("category_group") or "").casefold())
        assignment, split_issues = self._split_assignment()
        issues.extend(split_issues)
        return {
            "records": len(state), "review_status": dict(sorted(statuses.items())), "datasets": dict(sorted(datasets.items())),
            "categories": dict(sorted(categories.items())), "decisions": dict(sorted(decisions.items())), "reason_codes": dict(sorted(reasons.items())),
            "emoji_frequency": dict(emoji.most_common()), "question_reply_percent": round(100 * sum("?" in text for text in replies if text) / max(1, sum(bool(text) for text in replies)), 1),
            "average_response_length": round(sum(len(text) for text in replies) / max(1, len(replies)), 1),
            "reactive_count": len(current) - proactive, "proactive_count": proactive, "action_safety_coverage": action_rows,
            "validation": {"errors": [item for item in issues if item["severity"] == "error"], "warnings": [item for item in issues if item["severity"] != "error"]},
            "duplicates": self.duplicate_analysis(state), "split_assignment_count": len(assignment),
        }

    def preparation(self, state: Mapping[str, Mapping[str, Any]], *, version: str, dry_run: bool = True) -> dict[str, Any]:
        if not SAFE_VERSION.fullmatch(version):
            raise ReviewError("Version must contain only letters, digits, dot, underscore, or dash")
        issues = self.validate_state(state)
        issues.extend(self._project_source_audit_issues())
        synthetic = [(record_id, entry) for record_id, entry in state.items() if self.source_for(record_id) in {"v0", "v1"}]
        unresolved = [record_id for record_id, entry in synthetic if entry.get("status") == "NEEDS_REVIEW"]
        if unresolved:
            issues.append(_issue("error", "unresolved_reviews", None, f"{len(unresolved)} synthetic records still need review"))
        assignment, split_issues = self._split_assignment()
        issues.extend(split_issues)
        kept = [(record_id, entry) for record_id, entry in synthetic if entry.get("status") != "DROP"]
        missing_assignment = [record_id for record_id, _entry in kept if record_id not in assignment]
        if missing_assignment:
            issues.append(_issue("error", "missing_stable_split_assignment", None, f"{len(missing_assignment)} kept records are absent from existing split artifacts"))
        duplicate = self.duplicate_analysis(state)
        if duplicate["exact_clusters"]:
            issues.append(_issue("error", "exact_duplicates", None, "Exact duplicate clusters must be resolved before preparation"))
        blocking = [item for item in issues if item["severity"] == "error"]
        output = REVIEW_DIR / "approved" / version
        planned = [output / "peva_agency_synthetic_v0.jsonl", output / "peva_agency_synthetic_v1.jsonl", output / "peva_agency_pilot_train.jsonl", output / "peva_agency_pilot_validation.jsonl", output / "manifest.json", output / "change_report.json"]
        report: dict[str, Any] = {"version": version, "dry_run": dry_run, "ready": not blocking, "blocking_issues": blocking, "warnings": [item for item in issues if item["severity"] != "error"], "planned_files": [str(path.relative_to(ROOT)) for path in planned], "dropped_ids": [record_id for record_id, entry in synthetic if entry.get("status") == "DROP"], "kept_count": len(kept), "changes": []}
        for record_id, entry in synthetic:
            if entry["reviewed_record"] != entry["original_record"] or entry.get("status") == "DROP":
                report["changes"].append({"id": record_id, "source": self.source_for(record_id), "status": entry.get("status"), "original_sha256": _sha256(entry["original_record"]), "reviewed_sha256": _sha256(entry["reviewed_record"])})
        if blocking or dry_run:
            return report
        if output.exists():
            raise ReviewError(f"Refusing to overwrite existing reviewed dataset version: {output}")
        from trening.agency_dataset_builder import _canonical_json, _messages
        output.mkdir(parents=True)
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        train_rows: list[dict[str, Any]] = []
        validation_rows: list[dict[str, Any]] = []
        for record_id, entry in kept:
            row = _copy(entry["reviewed_record"])
            by_source[self.source_for(record_id)].append(row)
            prepared = {"id": record_id, "messages": _messages(row), "metadata": {"source": row["source"], "synthetic": bool(row["synthetic"]), "mode": row["mode"], "category_group": row["category_group"], "category": row["category"], "scenario_family": str(row.get("scenario_family") or row["category"])}}
            (train_rows if assignment[record_id] == "train" else validation_rows).append(prepared)
        def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
            content = "".join(_canonical_json(row) + "\n" for row in rows).encode("utf-8")
            _atomic_bytes_write(path, content)
            return hashlib.sha256(content).hexdigest()
        hashes = {
            "v0": write_jsonl(output / "peva_agency_synthetic_v0.jsonl", by_source["v0"]),
            "v1": write_jsonl(output / "peva_agency_synthetic_v1.jsonl", by_source["v1"]),
            "train": write_jsonl(output / "peva_agency_pilot_train.jsonl", sorted(train_rows, key=lambda row: row["id"])),
            "validation": write_jsonl(output / "peva_agency_pilot_validation.jsonl", sorted(validation_rows, key=lambda row: row["id"])),
        }
        report.update({"dry_run": False, "hashes": hashes, "train_count": len(train_rows), "validation_count": len(validation_rows)})
        _atomic_bytes_write(output / "manifest.json", _json_bytes({"schema_version": 1, "created_at": utc_now(), "version": version, "hashes": hashes, "preserved_existing_split_assignments": True, "source_review_export_sha256": _sha256(self.export_document(state))}))
        _atomic_bytes_write(output / "change_report.json", _json_bytes(report))
        return report

    def state_payload(self, state: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        records = []
        for record_id in sorted(state):
            entry = state[record_id]
            records.append({"id": record_id, "source_dataset": self.source_for(record_id), "original": entry["original_record"], "current": entry["reviewed_record"], "review": {key: entry.get(key) for key in ("status", "note", "modified_at", "revision_history")}})
        return {"records": records, "game_rules": self.game_rules, "review_store": str(self.review_store.relative_to(ROOT)) if self.review_store.is_relative_to(ROOT) else str(self.review_store)}


class StudioHandler(SimpleHTTPRequestHandler):
    """Small local API; all writes go through ReviewStudio's atomic storage."""

    studio: ReviewStudio
    state: dict[str, dict[str, Any]]
    lock = threading.RLock()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # SimpleHTTPRequestHandler otherwise serves the process CWD rather
        # than the Studio asset directory.
        super().__init__(*args, directory=str(REVIEW_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        print("[review-studio] " + (format % args))

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        rendered = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(rendered)))
        self.end_headers()
        self.wfile.write(rendered)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewError(f"Invalid JSON request: {exc}") from exc
        return _as_mapping(value, "request")

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            self.path = "/review_studio.html"
            return super().do_GET()
        with self.lock:
            if self.path == "/api/state":
                return self._send_json(self.studio.state_payload(type(self).state))
            if self.path == "/api/analysis":
                return self._send_json(self.studio.analytics(type(self).state))
            if self.path == "/api/export":
                payload = json.dumps(self.studio.export_document(type(self).state), ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename=peva_agency_manual_review.json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if self.path == "/api/benchmark":
                report: dict[str, Any] = {"command": "py -B agency_benchmark.py --base", "report": None}
                if BENCHMARK_REPORT.is_file():
                    try:
                        report["report"] = json.loads(BENCHMARK_REPORT.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        report["report"] = {"status": "invalid"}
                return self._send_json(report)
            if self.path == "/api/training":
                from trening.train_agency_pilot_base import CONFIG
                return self._send_json({"configuration": CONFIG, "command": "wsl -d Ubuntu --cd /mnt/d/projekty/lastZAI python3 -B train_peva_agency_pilot.py --dry-run", "start_command": "wsl -d Ubuntu --cd /mnt/d/projekty/lastZAI python3 -B train_peva_agency_pilot.py --run", "automatic_start": False})
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            with self.lock:
                if self.path == "/api/save":
                    record_id = str(body.get("id") or "")
                    self.studio.update_record(type(self).state, record_id=record_id, status=body.get("status"), note=body.get("note"), reviewed_record=_as_mapping(body.get("record"), "record"))
                    backup = self.studio.save_state(type(self).state)
                    return self._send_json({"ok": True, "backup": str(backup) if backup else None})
                if self.path == "/api/import":
                    document = _as_mapping(body.get("document"), "document")
                    type(self).state = self.studio.import_document(document)
                    backup = self.studio.save_state(type(self).state)
                    return self._send_json({"ok": True, "backup": str(backup) if backup else None})
                if self.path == "/api/prepare":
                    dry_run = bool(body.get("dry_run", True))
                    if not dry_run and body.get("confirmation") != "PREPARE REVIEWED DATASET":
                        raise ReviewError("Actual preparation requires exact confirmation: PREPARE REVIEWED DATASET")
                    return self._send_json(self.studio.preparation(type(self).state, version=str(body.get("version") or "reviewed-v1"), dry_run=dry_run))
                if self.path == "/api/benchmark/run":
                    if body.get("confirmation") != "RUN FROZEN BENCHMARK":
                        raise ReviewError("Running the benchmark requires exact confirmation: RUN FROZEN BENCHMARK")
                    # This is intentionally synchronous and opt-in: it runs
                    # only the existing frozen evaluator and never changes a
                    # dataset, score definition, or training state.
                    completed = subprocess.run(
                        [sys.executable, "-B", "agency_benchmark.py", "--base"], cwd=ROOT,
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    )
                    return self._send_json({"returncode": completed.returncode, "output": completed.stdout[-20000:]})
            self._send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except ReviewError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def serve(studio: ReviewStudio, *, port: int, open_browser: bool) -> None:
    if not FRONTEND.is_file():
        raise ReviewError(f"Missing Studio UI asset: {FRONTEND}")
    handler = type("ConfiguredStudioHandler", (StudioHandler,), {"studio": studio, "state": studio.load_state(), "directory": str(REVIEW_DIR)})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"PEva Agency Review Studio: {url}")
    print("Press Ctrl+C to stop. The server is local-only.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nReview Studio stopped.")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="Local PEva Agency Dataset Review Studio. Never starts training.")
    parser.add_argument("--serve", action="store_true", help="Launch the localhost-only Review Studio.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--review-file", type=Path, help="Import/read this review document instead of the normal store.")
    parser.add_argument("--game-rules", type=Path, help="Optional local JSON override for configurable Last Z review rules.")
    parser.add_argument("--validate", action="store_true", help="Print validation and analytics JSON.")
    parser.add_argument("--export", type=Path, help="Export the normalized review document without changing source datasets.")
    parser.add_argument("--prepare", metavar="VERSION", help="Prepare a new reviewed version only after all blocking checks pass.")
    parser.add_argument("--apply", action="store_true", help="With --prepare, write files instead of dry-run only.")
    args = parser.parse_args(argv)
    try:
        rules = None
        if args.game_rules:
            rules = _as_mapping(json.loads(args.game_rules.read_text(encoding="utf-8")), "game rules")
        studio = ReviewStudio(game_rules=rules)
        state = studio.load_state(args.review_file)
        if args.serve:
            serve(studio, port=args.port, open_browser=not args.no_browser)
            return 0
        if args.export:
            _atomic_json_write(args.export, studio.export_document(state))
            print(json.dumps({"exported": str(args.export)}, ensure_ascii=False))
            return 0
        if args.prepare:
            report = studio.preparation(state, version=args.prepare, dry_run=not args.apply)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ready"] else 2
        report = studio.analytics(state)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report["validation"]["errors"] else 2
    except (ReviewError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
