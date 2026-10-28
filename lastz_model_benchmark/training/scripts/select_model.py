#!/usr/bin/env python3
"""Select and document a trainable Hugging Face base model.

This is a provenance and download helper, not a trainer.  It never treats a
GGUF artifact as a trainable model.  By default it reads only a local candidate
registry and performs no network request; use ``--resolve`` or ``--download``
to explicitly permit Hugging Face access.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "1.0.0"
TRAINING_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = TRAINING_ROOT / "configs" / "model_candidates.json"
REPORTS_ROOT = TRAINING_ROOT / "outputs" / "reports"
MODELS_ROOT = TRAINING_ROOT / "models" / "hf"
SAFE_STEM = re.compile(r"[^A-Za-z0-9_.-]+")


class SelectionError(RuntimeError):
    """A recoverable selection or provenance error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_stem(value: str) -> str:
    cleaned = SAFE_STEM.sub("-", value.strip()).strip(".-")
    if not cleaned:
        raise SelectionError("Output stem must contain at least one letter or digit.")
    return cleaned[:100]


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def path_inside(path: Path, root: Path, label: str) -> Path:
    """Resolve a path and reject attempts to place pipeline state outside training/."""

    resolved_path = path.expanduser().resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise SelectionError("{} must stay under {} (got {}).".format(label, resolved_root, resolved_path)) from exc
    return resolved_path


def load_registry(config_path: Path) -> tuple[dict[str, Any], Path]:
    config_path = path_inside(config_path, TRAINING_ROOT, "Model registry")
    if not config_path.is_file():
        raise SelectionError("Model registry does not exist: {}".format(config_path))
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SelectionError("Model registry is not valid JSON: {}".format(exc)) from exc
    if not isinstance(data, dict):
        raise SelectionError("Model registry root must be a JSON object.")
    if data.get("schema_version") != 1:
        raise SelectionError("Unsupported model registry schema_version: {!r}".format(data.get("schema_version")))
    if not isinstance(data.get("candidates"), dict) or not data["candidates"]:
        raise SelectionError("Model registry must contain a non-empty candidates object.")
    return data, config_path


def validate_candidate(candidate_id: str, candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise SelectionError("Candidate {!r} must be a JSON object.".format(candidate_id))
    required_text = ("display_name", "hf_repo", "revision", "training_format")
    missing = [field for field in required_text if not isinstance(candidate.get(field), str) or not candidate[field].strip()]
    if missing:
        raise SelectionError("Candidate {!r} is missing non-empty field(s): {}.".format(candidate_id, ", ".join(missing)))
    if candidate["training_format"].lower() != "transformers":
        raise SelectionError(
            "Candidate {!r} has training_format={!r}; only a Transformers checkpoint may be selected for fine-tuning."
            .format(candidate_id, candidate["training_format"])
        )
    if candidate.get("allow_gguf_training") is not False:
        raise SelectionError("Candidate {!r} must explicitly set allow_gguf_training to false.".format(candidate_id))
    if ".gguf" in candidate["hf_repo"].lower() or ".gguf" in candidate["revision"].lower():
        raise SelectionError("Candidate {!r} points to a GGUF artifact, which is not trainable here.".format(candidate_id))
    for list_field in ("required_remote_files", "download_allow_patterns", "intended_phases"):
        value = candidate.get(list_field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise SelectionError("Candidate {!r} field {!r} must be a list of non-empty strings.".format(candidate_id, list_field))
    for source_field in ("merge_source", "upstream_provenance"):
        source = candidate.get(source_field)
        if source is None:
            continue
        if not isinstance(source, dict):
            raise SelectionError("Candidate {!r} field {!r} must be an object when supplied.".format(candidate_id, source_field))
        for field in ("hf_repo", "revision"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise SelectionError(
                    "Candidate {!r} {} is missing non-empty {!r}.".format(candidate_id, source_field, field)
                )
        if ".gguf" in source["hf_repo"].lower() or ".gguf" in source["revision"].lower():
            raise SelectionError("Candidate {!r} {} must not point to GGUF.".format(candidate_id, source_field))
    return candidate


def select_candidate(registry: dict[str, Any], explicit_candidate: str | None) -> tuple[str, dict[str, Any], str]:
    candidate_id = explicit_candidate or registry.get("default_candidate")
    selection_source = "--candidate" if explicit_candidate else "registry.default_candidate"
    if not candidate_id:
        raise SelectionError(
            "No candidate was selected. Pass --candidate <id>, or set a reviewed default_candidate in the model registry. "
            "The bundled registry intentionally has no implicit winner."
        )
    if not isinstance(candidate_id, str):
        raise SelectionError("Candidate id must be a string.")
    candidates = registry["candidates"]
    if candidate_id not in candidates:
        available = ", ".join(sorted(candidates))
        raise SelectionError("Unknown candidate {!r}. Available: {}.".format(candidate_id, available))
    return candidate_id, validate_candidate(candidate_id, candidates[candidate_id]), selection_source


def card_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "to_dict", None)
    if callable(as_dict):
        converted = as_dict()
        if isinstance(converted, Mapping):
            return dict(converted)
    return {}


def resolve_huggingface(candidate: dict[str, Any]) -> dict[str, Any]:
    """Resolve immutable revision, sibling list and licence through HF's API."""

    try:
        from huggingface_hub import HfApi  # type: ignore[import-not-found]
        import huggingface_hub  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SelectionError(
            "--resolve/--download requires huggingface_hub. Install it in the training environment first."
        ) from exc

    api = HfApi()
    try:
        try:
            info = api.model_info(candidate["hf_repo"], revision=candidate["revision"], files_metadata=True)
        except TypeError:  # Compatibility with older hub clients.
            info = api.model_info(candidate["hf_repo"], revision=candidate["revision"])
    except Exception as exc:
        raise SelectionError(
            "Could not resolve {repo}@{revision} through the Hugging Face API: {kind}: {message}".format(
                repo=candidate["hf_repo"], revision=candidate["revision"], kind=type(exc).__name__, message=exc
            )
        ) from exc

    card = card_to_dict(getattr(info, "cardData", None))
    raw_siblings = getattr(info, "siblings", None) or []
    files: list[dict[str, Any]] = []
    for sibling in raw_siblings:
        name = getattr(sibling, "rfilename", None) or getattr(sibling, "path", None)
        if not isinstance(name, str):
            continue
        lfs = getattr(sibling, "lfs", None)
        size = getattr(sibling, "size", None)
        if isinstance(lfs, Mapping) and lfs.get("size") is not None:
            size = lfs.get("size")
        files.append({"path": name, "size_bytes": size})
    files.sort(key=lambda item: item["path"])
    license_value = card.get("license") or getattr(info, "license", None)
    return {
        "requested": True,
        "status": "resolved",
        "huggingface_hub_version": getattr(huggingface_hub, "__version__", None),
        "repo_id": candidate["hf_repo"],
        "requested_revision": candidate["revision"],
        "resolved_revision": getattr(info, "sha", None) or candidate["revision"],
        "license": license_value,
        "private": getattr(info, "private", None),
        "gated": getattr(info, "gated", None),
        "files": files,
        "card_data": {key: card[key] for key in ("license", "library_name", "base_model", "pipeline_tag") if key in card},
    }


def verify_remote_files(candidate: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    resolved_names = {item["path"] for item in resolution.get("files", [])}
    required = candidate.get("required_remote_files", [])
    missing = [name for name in required if name not in resolved_names]
    gguf_files = [name for name in sorted(resolved_names) if name.lower().endswith(".gguf")]
    return {
        "required_remote_files": required,
        "missing_required_remote_files": missing,
        "repository_contains_gguf": bool(gguf_files),
        "gguf_files": gguf_files,
    }


def ensure_download_dir(candidate_id: str, supplied: str | None) -> Path:
    default_path = MODELS_ROOT / candidate_id
    if supplied:
        supplied_path = Path(supplied)
        selected = supplied_path if supplied_path.is_absolute() else TRAINING_ROOT / supplied_path
    else:
        selected = default_path
    return path_inside(selected, TRAINING_ROOT, "Download directory")


def download_checkpoint(candidate: dict[str, Any], resolution: dict[str, Any], destination: Path) -> dict[str, Any]:
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SelectionError("--download requires huggingface_hub. Install it in the training environment first.") from exc

    if any(destination.rglob("*.gguf")) or any(destination.rglob("*.GGUF")):
        raise SelectionError("Refusing to use a download directory that already contains GGUF files: {}".format(destination))
    patterns = list(candidate.get("download_allow_patterns", []))
    if not patterns:
        raise SelectionError("Candidate has no download_allow_patterns; refusing a broad download.")
    destination.mkdir(parents=True, exist_ok=True)
    revision = resolution.get("resolved_revision") or candidate["revision"]
    try:
        returned_path = snapshot_download(
            repo_id=candidate["hf_repo"],
            revision=revision,
            local_dir=str(destination),
            allow_patterns=patterns,
            ignore_patterns=["*.gguf", "*.GGUF"],
        )
    except Exception as exc:
        raise SelectionError("Checkpoint download failed: {}: {}".format(type(exc).__name__, exc)) from exc
    gguf_after = [str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file() and path.suffix.lower() == ".gguf"]
    if gguf_after:
        raise SelectionError("Download directory contains prohibited GGUF file(s): {}".format(", ".join(gguf_after)))
    downloaded_files = [path for path in destination.rglob("*") if path.is_file()]
    return {
        "requested": True,
        "status": "downloaded",
        "destination": str(destination),
        "huggingface_return_path": str(returned_path),
        "file_count": len(downloaded_files),
        "total_bytes": sum(path.stat().st_size for path in downloaded_files),
        "revision": revision,
    }


def render_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate")
    lines = [
        "# Last Z trainable base-model selection",
        "",
        "- Generated (UTC): `{}`".format(report["generated_at_utc"]),
        "- Script version: `{}`".format(report["script_version"]),
        "- Network access requested: `{}`".format(report["scope"]["network_access_requested"]),
        "- Download requested: `{}`".format(report["scope"]["download_requested"]),
        "- This report selects a trainable Transformers checkpoint only. It does not train a model, execute a game action, or select a production winner.",
        "",
    ]
    if report.get("error"):
        lines.extend(["## Error", "", "{}".format(report["error"]), ""])
        return "\n".join(lines)
    assert candidate is not None
    lines.extend(
        [
            "## Configured candidate",
            "",
            "| Field | Value |",
            "| --- | --- |",
            "| Candidate id | `{}` |".format(report["candidate_id"]),
            "| Selection source | `{}` |".format(report["selection_source"]),
            "| Name | {} |".format(candidate["display_name"]),
            "| Hugging Face repository | `{}` |".format(candidate["hf_repo"]),
            "| Requested revision | `{}` |".format(candidate["revision"]),
            "| Training format | `{}` |".format(candidate["training_format"]),
            "| GGUF training allowed | `{}` |".format(candidate["allow_gguf_training"]),
            "| Registry status | `{}` |".format(candidate.get("selection_status", "not specified")),
            "",
            "## Licence and provenance",
            "",
            "- Expected governing licence: `{}`".format(candidate.get("expected_license", "not specified")),
        ]
    )
    if candidate.get("card_license"):
        lines.append("- Checkpoint card licence field: `{}`".format(candidate["card_license"]))
    if candidate.get("license_note"):
        lines.append("- Licence caveat: {}".format(candidate["license_note"]))
    merge_source = candidate.get("merge_source")
    if merge_source:
        lines.append("- Merge source: `{}` @ `{}`".format(merge_source["hf_repo"], merge_source["revision"]))
    upstream_source = candidate.get("upstream_provenance")
    if upstream_source:
        lines.append("- Upstream provenance: `{}` @ `{}`".format(upstream_source["hf_repo"], upstream_source["revision"]))
    lines.extend(
        [
            "",
            "## Resolution",
            "",
        ]
    )
    resolution = report["resolution"]
    if resolution["status"] == "not_requested":
        lines.append("No Hugging Face request was made. Run with `--resolve` to record the immutable revision, files, and licence.")
    else:
        lines.extend(
            [
                "- Resolved revision: `{}`".format(resolution.get("resolved_revision") or "not available"),
                "- Licence reported by API: `{}`".format(resolution.get("license") or "not reported"),
                "- Files reported: `{}`".format(len(resolution.get("files", []))),
                "- Repository contains GGUF files: `{}`".format(report.get("remote_validation", {}).get("repository_contains_gguf", False)),
            ]
        )
        missing = report.get("remote_validation", {}).get("missing_required_remote_files", [])
        if missing:
            lines.append("- Missing required files: `{}`".format(", ".join(missing)))
    lines.extend(["", "## Download", ""])
    download = report["download"]
    if download["status"] == "not_requested":
        lines.append("No checkpoint was downloaded.")
    else:
        lines.extend(
            [
                "- Status: `{}`".format(download["status"]),
                "- Destination: `{}`".format(download.get("destination", "not available")),
                "- Files: `{}`".format(download.get("file_count", "not available")),
                "- Bytes: `{}`".format(download.get("total_bytes", "not available")),
            ]
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- The registry entry must declare `training_format: transformers` and `allow_gguf_training: false`.",
            "- The downloader allow-lists source checkpoint files and ignores `*.gguf`.",
            "- A separate evaluation phase must determine whether this candidate becomes a production recommendation.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select a configured trainable model; network access is opt-in and GGUF checkpoints are rejected."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Registry JSON under training/ (default: %(default)s).")
    parser.add_argument("--candidate", help="Candidate id from the registry. Required if registry.default_candidate is null.")
    parser.add_argument("--list", action="store_true", help="List configured candidate ids and exit without a report or network request.")
    parser.add_argument("--resolve", action="store_true", help="Explicitly query the Hugging Face API for revision, files and licence.")
    parser.add_argument(
        "--verify-remote",
        dest="resolve",
        action="store_true",
        help="Alias for --resolve, retained for the documented Phase 1 command sequence.",
    )
    parser.add_argument("--download", action="store_true", help="Explicitly resolve then download allow-listed Transformers checkpoint files.")
    parser.add_argument("--download-dir", help="Optional directory below training/ for --download (default: training/models/hf/<candidate>).")
    parser.add_argument("--output-stem", help="Optional safe filename stem, written only below training/outputs/reports.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        registry, config_path = load_registry(Path(args.config))
        if args.list:
            for candidate_id, raw_candidate in sorted(registry["candidates"].items()):
                candidate = validate_candidate(candidate_id, raw_candidate)
                print("{}\t{}\t{}".format(candidate_id, candidate["display_name"], candidate["hf_repo"]))
            return 0

        candidate_id, candidate, selection_source = select_candidate(registry, args.candidate)
        if args.download and not args.resolve:
            args.resolve = True  # A download is explicit permission to resolve its immutable source first.
        report: dict[str, Any] = {
            "schema_version": 1,
            "script": "select_model.py",
            "script_version": SCRIPT_VERSION,
            "generated_at_utc": utc_now(),
            "scope": {
                "network_access_requested": bool(args.resolve),
                "download_requested": bool(args.download),
                "training": False,
                "gguf_training": False,
                "adb_or_bluestacks": False,
            },
            "config_path": str(config_path),
            "candidate_id": candidate_id,
            "selection_source": selection_source,
            "candidate": candidate,
            "resolution": {"requested": False, "status": "not_requested"},
            "remote_validation": None,
            "download": {"requested": bool(args.download), "status": "not_requested"},
            "production_recommendation": "not_made",
        }
        if args.resolve:
            resolution = resolve_huggingface(candidate)
            report["resolution"] = resolution
            remote_validation = verify_remote_files(candidate, resolution)
            report["remote_validation"] = remote_validation
            if remote_validation["missing_required_remote_files"]:
                raise SelectionError(
                    "Resolved repository is missing required file(s): {}.".format(
                        ", ".join(remote_validation["missing_required_remote_files"])
                    )
                )
        if args.download:
            destination = ensure_download_dir(candidate_id, args.download_dir)
            report["download"] = download_checkpoint(candidate, report["resolution"], destination)
    except SelectionError as exc:
        report = {
            "schema_version": 1,
            "script": "select_model.py",
            "script_version": SCRIPT_VERSION,
            "generated_at_utc": utc_now(),
            "scope": {
                "network_access_requested": bool(getattr(args, "resolve", False) or getattr(args, "download", False)),
                "download_requested": bool(getattr(args, "download", False)),
                "training": False,
                "gguf_training": False,
                "adb_or_bluestacks": False,
            },
            "error": str(exc),
        }

    try:
        stem = safe_stem(args.output_stem) if args.output_stem else "model_selection_" + timestamp()
        json_path = REPORTS_ROOT / (stem + ".json")
        markdown_path = REPORTS_ROOT / (stem + ".md")
        if json_path.exists() or markdown_path.exists():
            raise SelectionError("Report already exists: {}".format(stem))
        write_report(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        write_report(markdown_path, render_markdown(report))
    except SelectionError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    print("Wrote JSON report: {}".format(json_path))
    print("Wrote Markdown report: {}".format(markdown_path))
    if report.get("error"):
        print("ERROR: {}".format(report["error"]), file=sys.stderr)
        return 2
    print("Selected configured candidate {!r}; no production winner was declared.".format(report["candidate_id"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
