#!/usr/bin/env python3
"""Safely rebuild the active Last Z ChatNetManagerBase patch after an update.

The updater discovers the bundle through the packaged ``gameres`` manifest;
no release hash, bundle id, path id, CRC, or size is baked into the logic.
It stops rather than guessing if the Lua callback's known bytecode shape has
changed.  It writes only a new output directory and never overwrites an APK.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import UnityPy


LASTZ = b"\x1bLua\x03\x54"
LUA54 = b"\x1bLua\x54"
CALLBACK_MARKER = b"[callback] (%d) - %s"
MOVE_7_1 = bytes.fromhex("80 03 01 00")
MOVE_7_2 = bytes.fromhex("80 03 02 00")
TARGET_DIRECTORY = "Assets/SlgEngine/Main/LuaTxt/Chat/Controller"
TARGET_ASSET = "ChatNetManagerBase.bytes"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def raw_script(asset) -> bytes:
    value = asset.m_Script
    return value.encode("utf-8", "surrogateescape") if isinstance(value, str) else bytes(value)


def split_wrapper(data: bytes) -> tuple[bytes, bytes]:
    if data.startswith(b"UnityRawUnityFS"):
        return b"UnityRaw", data[len(b"UnityRaw"):]
    if data.startswith(b"UnityFS"):
        return b"", data
    raise RuntimeError("Unexpected AssetBundle header; refusing patch.")


def normalize(raw: bytes) -> bytes:
    if not raw.startswith(LASTZ):
        raise RuntimeError("ChatNetManagerBase is not a Last Z Lua 5.4 chunk.")
    result = raw[:4] + raw[5:]
    if not result.startswith(LUA54):
        raise RuntimeError("Unexpected normalized Lua header.")
    return result


def lastz_chunk(stock: bytes) -> bytes:
    if not stock.startswith(LUA54):
        raise RuntimeError("Refusing to serialize a non-Lua-5.4 patch.")
    return stock[:4] + b"\x03" + stock[4:]


def lua_short_record(text: bytes) -> bytes:
    if len(text) + 1 >= 128:
        raise RuntimeError("Unsupported long Lua string constant.")
    return bytes([0x80 | (len(text) + 1)]) + text


def parse_manifest(data: bytes) -> tuple[dict[int, str], list[tuple[int, str, int]], dict[int, str]]:
    text = data.decode("utf-8")
    section = ""
    dirs: dict[int, str] = {}
    assets: list[tuple[int, str, int]] = []
    bundles: dict[int, str] = {}
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line
            continue
        if not line or line.startswith("#"):
            continue
        if section == "[Assets]":
            fields = line.split(",")
            if len(fields) == 3 and "/" in fields[1]:
                _directory_id, leaf = fields[1].split("/", 1)
                assets.append((int(fields[0]), leaf, int(fields[2])))
            continue
        if section == "[Bundles]":
            fields = line.split(",")
            if len(fields) >= 2:
                bundles[int(fields[0])] = fields[1]
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if section == "[Directories]":
            dirs[int(key)] = value
    return dirs, assets, bundles


def target_bundle_name(manifest: bytes) -> str:
    """Return the one active bundle that supplies ChatNetManagerBase."""
    dirs, _assets, bundles = parse_manifest(manifest)
    hits: list[int] = []
    for line in manifest.decode("utf-8").splitlines():
        if not line or line.startswith("#") or "," not in line:
            continue
        fields = line.split(",")
        if len(fields) != 3 or not fields[1].endswith("/" + TARGET_ASSET):
            continue
        directory_id = int(fields[1].split("/", 1)[0])
        if dirs.get(directory_id) == TARGET_DIRECTORY:
            hits.append(int(fields[2]))
    if len(hits) != 1:
        raise RuntimeError(f"Expected one active ChatNetManagerBase manifest route, found {hits!r}")
    bundle_name = bundles.get(hits[0])
    if not bundle_name:
        raise RuntimeError("Manifest route references no bundle name.")
    return bundle_name


def discover_bundle(pack_apk: Path) -> tuple[str, bytes, str, bytes, str, bytes]:
    with zipfile.ZipFile(pack_apk) as archive:
        candidates = [x for x in archive.namelist() if x.endswith("/gameres")]
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one packaged gameres, found {candidates!r}")
        manifest = archive.read(candidates[0])
        bundle_name = target_bundle_name(manifest)
        entry = f"assets/AssetBundles/{bundle_name}"
        if entry not in archive.namelist():
            raise RuntimeError(f"Active bundle is not in split APK: {entry}")
        version_entry = candidates[0] + ".version"
        if version_entry not in archive.namelist():
            raise RuntimeError(f"Packaged gameres.version is missing: {version_entry}")
        return (
            entry,
            archive.read(entry),
            candidates[0],
            manifest,
            version_entry,
            archive.read(version_entry),
        )


def patch_manifest(
    manifest: bytes,
    version: bytes,
    bundle_name: str,
    replacement: bytes,
    manifest_version: str | None = None,
) -> tuple[bytes, bytes]:
    """Update only the active bundle's CRC32/size and the matching manifest version."""
    try:
        text = manifest.decode("utf-8")
        version_text = version.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Packaged manifest/version encoding changed; refusing patch.") from exc

    changed = 0
    rewritten: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        fields = body.split(",")
        if len(fields) >= 4 and fields[1] == bundle_name:
            fields[2] = str(zlib.crc32(replacement) & 0xFFFFFFFF)
            fields[3] = str(len(replacement))
            body = ",".join(fields)
            changed += 1
        rewritten.append(body + ending)
    if changed != 1:
        raise RuntimeError(f"Expected exactly one manifest entry to update, found {changed}.")
    patched_manifest = "".join(rewritten).encode("utf-8")

    parts = version_text.split(",")
    if len(parts) != 3 or not parts[0].isdigit():
        raise RuntimeError(f"Unexpected gameres.version format: {version_text!r}")
    selected_version = manifest_version if manifest_version is not None else parts[0]
    if not selected_version.isdigit():
        raise RuntimeError(f"Invalid requested gameres version: {selected_version!r}")
    patched_version = f"{selected_version},{len(patched_manifest)},{zlib.crc32(patched_manifest) & 0xFFFFFFFF}".encode("ascii")
    return patched_manifest, patched_version


def patch_bundle(source: bytes, init_canary: bool = False) -> tuple[bytes, dict[str, str | int]]:
    wrapper, inner = split_wrapper(source)
    env = UnityPy.load(inner)
    matches = []
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        asset = obj.read()
        if asset.m_Name == "ChatNetManagerBase" and raw_script(asset).startswith(LASTZ):
            matches.append((obj, asset))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one Lua ChatNetManagerBase, found {len(matches)}")
    obj, asset = matches[0]
    before = raw_script(asset)
    stock = normalize(before)
    chatprint = lua_short_record(b"ChatPrint")
    print_record = lua_short_record(b"print")
    marker_hits = [i for i in range(len(stock)) if stock.startswith(CALLBACK_MARKER, i)]
    if len(marker_hits) != 1:
        raise RuntimeError("Callback marker is not unique; refusing operand guess.")
    marker = marker_hits[0]
    start = max(0, marker - 1024)

    def moves_before_marker(opcode: bytes) -> list[int]:
        positions: list[int] = []
        pos = stock.find(opcode, start, marker)
        while pos >= 0:
            positions.append(pos)
            pos = stock.find(opcode, pos + 1, marker)
        return positions

    count = stock.count(chatprint)
    expected_old_moves = moves_before_marker(MOVE_7_1)
    expected_patched_moves = moves_before_marker(MOVE_7_2)
    if count == 0:
        # A previous successful PEva patch deliberately makes this exact
        # constant/operand change.  Treat it as a verified no-op, not as an
        # unknown game update.  This lets the one-click launcher survive a
        # restart without trying to sign/install the same game again.
        if stock.count(print_record) >= 1 and not expected_old_moves and len(expected_patched_moves) == 1:
            return source, {
                "path_id": int(obj.path_id),
                "before_sha256": sha(before),
                "after_sha256": sha(before),
                "chatprint_constants": 0,
                "callback_move_offset": expected_patched_moves[0],
                "init_canary": init_canary,
                "already_patched": True,
            }
        raise RuntimeError("ChatPrint constants were not found and no verified PEva patch signature exists.")
    if count < 1:
        raise RuntimeError("ChatPrint constants were not found; unknown update shape.")
    patched = stock.replace(chatprint, print_record)
    if len(expected_old_moves) != 1:
        raise RuntimeError(f"Expected one MOVE 7 1 before callback marker, found {expected_old_moves!r}")
    offset = expected_old_moves[0]
    patched = patched[:offset] + MOVE_7_2 + patched[offset + 4:]
    if patched.count(chatprint) != 0 or patched.count(print_record) < count:
        raise RuntimeError("Lua string patch validation failed.")
    if init_canary:
        init_record = lua_short_record(b"ChatNetManager:Init %s")
        canary_record = lua_short_record(b"PEVA_CHATNET_INIT_OK!!")
        if len(init_record) != len(canary_record):
            raise RuntimeError("Init canary must preserve its Lua constant record length.")
        if patched.count(init_record) != 1:
            raise RuntimeError("Expected exactly one ChatNetManager Init constant for canary.")
        patched = patched.replace(init_record, canary_record)
    replacement = lastz_chunk(patched)
    asset.m_Script = replacement.decode("utf-8", "surrogateescape")
    asset.save()
    result = wrapper + env.file.save(packer="original")

    check_env = UnityPy.load(split_wrapper(result)[1])
    check = [
        raw_script(o.read()) for o in check_env.objects
        if o.type.name == "TextAsset" and o.read().m_Name == "ChatNetManagerBase"
        and raw_script(o.read()).startswith(LASTZ)
    ]
    if check != [replacement]:
        raise RuntimeError("Saved AssetBundle failed exact TextAsset round-trip verification.")
    return result, {
        "path_id": int(obj.path_id), "before_sha256": sha(before), "after_sha256": sha(replacement),
        "chatprint_constants": count, "callback_move_offset": offset, "init_canary": init_canary,
        "already_patched": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack-apk", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--init-canary", action="store_true", help="Replace the Init log string with a same-size runtime proof marker.")
    ap.add_argument("--manifest-version", help="Use a verified current CDN gameres version to prevent it replacing the signed manifest.")
    args = ap.parse_args()
    pack = args.pack_apk.resolve()
    if not pack.is_file():
        raise SystemExit(f"Missing pack APK: {pack}")
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    entry, source, manifest_entry, manifest, version_entry, version = discover_bundle(pack)
    patched, details = patch_bundle(source, args.init_canary)
    bundle_out = out / Path(entry).name.replace(".bundle", ".patched.bundle")
    bundle_out.write_bytes(patched)
    manifest_out, version_out = patch_manifest(manifest, version, Path(entry).name, patched, args.manifest_version)
    gameres_out = out / "gameres.patched"
    gameres_version_out = out / "gameres.version.patched"
    gameres_out.write_bytes(manifest_out)
    gameres_version_out.write_bytes(version_out)
    metadata = {
        "pack_apk": str(pack),
        "bundle_entry": entry,
        "bundle_name": Path(entry).name,
        "stock_bundle_sha256": sha(source),
        "stock_bundle_size": len(source),
        "patched_bundle": str(bundle_out),
        "patched_bundle_sha256": sha(patched),
        "patched_bundle_size": len(patched),
        "patched_bundle_crc32": zlib.crc32(patched) & 0xFFFFFFFF,
        "gameres_entry": manifest_entry,
        "stock_gameres_sha256": sha(manifest),
        "patched_gameres": str(gameres_out),
        "patched_gameres_sha256": sha(manifest_out),
        "patched_gameres_size": len(manifest_out),
        "patched_gameres_crc32": zlib.crc32(manifest_out) & 0xFFFFFFFF,
        "gameres_version_entry": version_entry,
        "patched_gameres_version": str(gameres_version_out),
        "patched_gameres_version_text": version_out.decode("ascii"),
        "details": details,
        "init_canary": args.init_canary,
        "requested_manifest_version": args.manifest_version,
    }
    metadata_out = out / "patch_metadata.json"
    metadata_out.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("active APK entry:", entry)
    print("stock bundle sha256:", sha(source))
    print("patched bundle sha256:", sha(patched))
    print("patched bundle:", bundle_out)
    print("patched gameres:", gameres_out)
    print("patched gameres.version:", gameres_version_out)
    print("patch metadata:", metadata_out)
    for key, value in details.items():
        print(f"{key}: {value}")
    print("OK: replace the bundle, gameres, and gameres.version APK entries, then sign the split.")


if __name__ == "__main__":
    main()
