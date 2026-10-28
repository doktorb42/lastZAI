#!/usr/bin/env python3
"""Compatibility adapter for the archived fail-closed patch engine.

The old helper imported a second independent implementation from
update_lastz_chat_capture.py, which could disagree with the status checker.
The adapter intentionally shares the exact discovery, patch verification,
bundle serialization, manifest validation, and ADB deployment code with the
archive/legacy_runtime/update_patch.py. Does not uninstall/reinstall game APKs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from archive.legacy_runtime import update_patch as engine  # noqa: E402


def patch_active_cache(*, adb: Path, device_id: str, package: str, output_dir: Path, dry_run: bool) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(
        adb=str(adb), device=device_id, package=package,
        base=f'/sdcard/Android/data/{package}', workspace=str(output_dir),
        start=False, build_only=False,
    )
    unity = engine.require_unitypy()
    engine.require_adb(args.adb, args.device)
    # Applying to live files requires freezing the updater/game before snapshot.
    if not dry_run:
        engine.adb_shell(['am', 'force-stop', package], args.adb, args.device)
    snapshot = engine.snapshot_device(args, unity, label='dry_run' if dry_run else 'apply')
    bundle, manifest, sidecar, result = engine.build_patch(args, unity, snapshot)
    metadata_path = output_dir / 'patch_metadata.json'
    metadata = {
        'method': 'UNIFIED_BUNDLE_ENGINE',
        'device': device_id, 'package': package,
        'bundle_name': snapshot.bundle_row.name,
        'already_patched': result.already_patched,
        'dry_run': dry_run,
        'backup': str(snapshot.run_dir),
        'before_bundle_sha256': engine.sha256_bytes(snapshot.bundle),
        'after_bundle_sha256': engine.sha256_bytes(bundle.read_bytes()),
        'before_manifest_sha256': engine.sha256_bytes(snapshot.manifest),
        'after_manifest_sha256': engine.sha256_bytes(manifest.read_bytes()),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    if result.already_patched:
        print(f'ACTIVE_CACHE_ALREADY_PATCHED={metadata_path}')
        return 'already_patched'
    if dry_run:
        print(f'ACTIVE_CACHE_READY={metadata_path}')
        return 'ready'
    engine.deploy_patch(args, snapshot, bundle, manifest, sidecar)
    # Validate bytes in the same AssetBundles path that was updated.
    verified = engine.snapshot_device(args, unity, label='verify')
    check = engine.patch_chatnet_stock(verified.target.original_stock)
    if not check.already_patched:
        raise engine.PatchAbort('Patch not present immediately after deployment')
    print(f'ACTIVE_CACHE_PATCH_OK={metadata_path}')
    return 'patched'


def main() -> int:
    parser = argparse.ArgumentParser(description='Last Z chat bundle patch compatibility adapter')
    parser.add_argument('--adb', type=Path, required=True)
    parser.add_argument('--device', required=True)
    parser.add_argument('--package', default=engine.DEFAULT_PACKAGE)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        patch_active_cache(adb=args.adb, device_id=args.device, package=args.package,
                           output_dir=args.out_dir, dry_run=args.dry_run)
    except Exception as exc:
        # Keep old PowerShell selector's documented 'not found' result.
        if str(exc) == 'ACTIVE_CACHE_NOT_FOUND':
            print('ACTIVE_CACHE_NOT_FOUND')
            return 4
        print(f'ACTIVE_CACHE_PATCH_FAILED={type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
