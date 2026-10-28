"""Write a pre-cleanup filesystem inventory without changing project inputs.

The report deliberately hashes deployment artifacts and all same-size candidates
for duplicate detection.  Errors (for example an unavailable cached model file)
are recorded in the report instead of stopping the audit.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


IMPORTANT_EXTENSIONS = {
    ".bundle", ".db", ".json", ".jsonl", ".ps1", ".py", ".bat", ".sh",
}
DUPLICATE_CANDIDATE_EXTENSIONS = {
    ".apk", ".bundle", ".db", ".json", ".jsonl", ".zip", ".7z",
    ".ps1", ".py", ".bat", ".sh", ".png",
}
MAX_DUPLICATE_CANDIDATE_BYTES = 512 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def format_size(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def is_model_cache(root: Path, path: Path) -> bool:
    """Avoid re-hashing downloaded/training weights during a project audit."""
    relative = path.relative_to(root)
    parts = {part.lower() for part in relative.parts}
    if {"hf_cache", "unsloth_compiled_cache", "__pycache__"} & parts:
        return True
    return tuple(part.lower() for part in relative.parts[:3]) == (
        "lastz_model_benchmark", "training", "models"
    )


def is_active_patch_apk(root: Path, path: Path) -> bool:
    """Identify APKs required by the known working updater or its current build."""
    relative = path.relative_to(root)
    parts = tuple(part.lower() for part in relative.parts)
    return len(parts) == 1 or (
        len(parts) >= 3 and parts[0:2] in {("apk", "current"), ("patch", "current"), ("patch", "builds")}
    )


def is_final_solution_area(root: Path, path: Path) -> bool:
    """Limit expensive hashes to the live patch and its directly supporting code."""
    relative = path.relative_to(root)
    if len(relative.parts) == 1:
        return True
    return relative.parts[0].lower() in {"apk", "patch", "tools", "data", "config", "ui"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output.resolve()
    files: list[tuple[Path, int, float, str]] = []
    errors: list[str] = []
    by_size: dict[int, list[Path]] = defaultdict(list)

    for current, _dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in names:
            path = current_path / name
            if path.resolve() == output:
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                errors.append(f"STAT_ERROR | {path} | {exc}")
                continue
            if not path.is_file():
                continue
            extension = path.suffix.lower() or "[none]"
            files.append((path.resolve(), stat.st_size, stat.st_mtime, extension))
            if stat.st_size:
                by_size[stat.st_size].append(path.resolve())

    hashes: dict[Path, str] = {}
    to_hash: set[Path] = {
        path for path, _size, _mtime, extension in files
        if extension in IMPORTANT_EXTENSIONS
        and is_final_solution_area(root, path)
        and not is_model_cache(root, path)
    }
    to_hash.update(
        path for path, _size, _mtime, extension in files
        if extension == ".apk" and is_active_patch_apk(root, path)
    )
    for candidates in by_size.values():
        if len(candidates) > 1:
            to_hash.update(
                path for path in candidates
                if path.stat().st_size <= MAX_DUPLICATE_CANDIDATE_BYTES
                and is_final_solution_area(root, path)
                and not is_model_cache(root, path)
                and path.suffix.lower() in DUPLICATE_CANDIDATE_EXTENSIONS
            )

    for index, path in enumerate(sorted(to_hash, key=lambda item: str(item).lower()), 1):
        try:
            hashes[path] = sha256(path)
        except OSError as exc:
            errors.append(f"HASH_ERROR | {path} | {exc}")
        if index % 100 == 0:
            print(f"hashed {index}/{len(to_hash)}", flush=True)

    duplicate_groups: dict[str, list[Path]] = defaultdict(list)
    for path, digest in hashes.items():
        if len(by_size[path.stat().st_size]) > 1:
            duplicate_groups[digest].append(path)

    total_size = sum(size for _path, size, _mtime, _extension in files)
    lines = [
        "# File inventory — before cleanup",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Root: `{root}`",
        f"Files: {len(files):,}",
        f"Total bytes: {total_size:,} ({format_size(total_size)})",
        f"Hashed artifacts/candidates: {len(hashes):,}",
        "SHA-256 scope: the final updater area (root, apk/current, patch, tools, "
        "data, config, ui), its active APKs, "
        "bundles, databases, scripts, and same-size candidates (APK, bundle, DB, "
        "JSONL, archives, scripts, and PNG evidence) up to 512 MiB. Model workspaces, "
        "archives, and superseded experiments are fully listed but deliberately not "
        "re-hashed.",
        "",
        "## Files",
        "",
        "| Full path | Size (bytes) | Extension | Modified (UTC) | SHA-256 |",
        "|---|---:|---|---|---|",
    ]
    for path, size, mtime, extension in sorted(files, key=lambda item: str(item[0]).lower()):
        modified = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        hash_text = hashes.get(path, "")
        if not hash_text and is_model_cache(root, path):
            hash_text = "not computed (model/cache)"
        lines.append(f"| `{path}` | {size} | `{extension}` | {modified} | `{hash_text}` |")

    lines += ["", "## Duplicate groups (SHA-256)", ""]
    groups = [
        (digest, paths) for digest, paths in duplicate_groups.items() if len(paths) > 1
    ]
    if groups:
        for digest, paths in sorted(groups):
            lines.append(f"### `{digest}`")
            lines.append("")
            for path in sorted(paths, key=lambda item: str(item).lower()):
                lines.append(f"- `{path}`")
            lines.append("")
    else:
        lines.append("No SHA-256 duplicate groups were found among hashed candidates.")

    lines += ["", "## Audit errors", ""]
    lines.extend(f"- `{error}`" for error in errors) if errors else lines.append("None.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
