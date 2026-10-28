#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Last Z automatic chat-capture patch updater.

Updater revision: 1.2 - fixes MOVE verification and tolerates the known post-startup gameres.version CRC/size rewrite while still verifying the active manifest and bundle.

What it does:
  1) Reads the CURRENT gameres + gameres.version from BlueStacks.
  2) Finds the project ChatNetManager bundle from gameres (no hardcoded bundle name/path_id).
  3) Pulls that bundle and discovers the active ChatNetManager TextAsset by signatures:
       "[callback] (%d) - %s"
       "websocket push : "
  4) Verifies the known Lua 5.4 layout.
  5) Applies the two proven changes:
       ChatPrint -> print
       MOVE 7 1  -> MOVE 7 2 in __onCallback
     so callback logs contain the raw JSON payload.
  6) Rebuilds the bundle with UnityPy and verifies exact TextAsset round-trip.
  7) Updates only CRC32 + size for that bundle in gameres.
  8) Creates matching gameres.version = VERSION,SIZE,CRC32.
  9) Optionally deploys the patched files to BlueStacks.

IMPORTANT:
- This is intentionally fail-closed. If a future game update changes the
  ChatNetManager structure, it STOPS instead of patching unknown bytecode.
- Let a normal game update finish first, then run this script.
- The script backs up every pulled original before writing/deploying anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_ADB = r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"
DEFAULT_DEVICE = "emulator-5554"
DEFAULT_PACKAGE = "com.readygo.barrel.gp"
DEFAULT_ACTIVITY = "com.im30.aps.debug.UnityPlayerActivityCustom"
DEFAULT_BASE = "/sdcard/Android/data/com.readygo.barrel.gp"
DEFAULT_WORKSPACE = r"D:\LastZChatTest\auto_patch"

UNITYRAW = b"UnityRaw"
UNITYFS = b"UnityFS"
CUSTOM_LUA_PREFIX = b"\x1bLua\x03\x54"
STOCK_LUA_PREFIX = b"\x1bLua\x54"

CALLBACK_MARKER = b"[callback] (%d) - %s"
WEBSOCKET_MARKER = b"websocket push : "

# Lua 5.4, little endian:
#   MOVE 7 1 -> 0x00010380 -> 80 03 01 00
#   MOVE 7 2 -> 0x00020380 -> 80 03 02 00
MOVE_7_1 = bytes.fromhex("80 03 01 00")
MOVE_7_2 = bytes.fromhex("80 03 02 00")


class PatchAbort(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def crc32_u32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def run(cmd, *, check=True, capture=False, text=True):
    print("[CMD]", subprocess.list2cmdline([str(x) for x in cmd]))
    return subprocess.run(
        [str(x) for x in cmd],
        check=check,
        capture_output=capture,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
    )


def adb_cmd(args, adb: str, device: str, *, check=True, capture=False):
    return run([adb, "-s", device, *args], check=check, capture=capture)


def adb_shell(args, adb: str, device: str, *, check=True, capture=False):
    return adb_cmd(["shell", *args], adb, device, check=check, capture=capture)


def require_adb(adb: str, device: str) -> None:
    if not Path(adb).exists():
        raise PatchAbort(f"ADB not found: {adb}")
    p = adb_cmd(["get-state"], adb, device, check=False, capture=True)
    if p.returncode != 0 or "device" not in (p.stdout or ""):
        raise PatchAbort(
            f"ADB device {device!r} is not ready.\n"
            f"stdout={p.stdout!r}\nstderr={p.stderr!r}"
        )


def require_unitypy():
    try:
        import UnityPy  # type: ignore
        return UnityPy
    except Exception as e:
        raise PatchAbort(
            "UnityPy is required.\n"
            "Install it with:\n"
            "  py -m pip install -U UnityPy\n"
            f"Original error: {e}"
        )


def pull(adb: str, device: str, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    p = adb_cmd(["pull", remote, str(local)], adb, device, check=False, capture=True)
    if p.returncode != 0 or not local.exists():
        raise PatchAbort(
            f"Failed to pull {remote}\nstdout={p.stdout}\nstderr={p.stderr}"
        )
    print(p.stdout.strip())


def push(adb: str, device: str, local: Path, remote: str) -> None:
    if not local.exists():
        raise PatchAbort(f"Local file missing: {local}")
    p = adb_cmd(["push", str(local), remote], adb, device, check=False, capture=True)
    if p.returncode != 0:
        raise PatchAbort(
            f"Failed to push {local} -> {remote}\n"
            f"stdout={p.stdout}\nstderr={p.stderr}"
        )
    print(p.stdout.strip())


def remote_cat(adb: str, device: str, remote: str) -> Optional[str]:
    p = adb_shell(["cat", remote], adb, device, check=False, capture=True)
    if p.returncode != 0:
        return None
    return (p.stdout or "").strip()


def remote_sha256(adb: str, device: str, remote: str) -> Optional[str]:
    p = adb_shell(["sha256sum", remote], adb, device, check=False, capture=True)
    if p.returncode != 0:
        return None
    out = (p.stdout or "").strip()
    if not out:
        return None
    return out.split()[0]


def get_package_versions(adb: str, device: str, package: str) -> dict:
    p = adb_shell(["dumpsys", "package", package], adb, device, check=False, capture=True)
    text = p.stdout or ""
    info = {}
    m = re.search(r"\bversionName=([^\s]+)", text)
    if m:
        info["versionName"] = m.group(1)
    m = re.search(r"\bversionCode=(\d+)", text)
    if m:
        info["versionCode"] = m.group(1)
    return info


def parse_sidecar(text: str) -> tuple[int, int, int]:
    try:
        a, b, c = text.strip().split(",")
        return int(a), int(b), int(c)
    except Exception:
        raise PatchAbort(f"Unexpected gameres.version format: {text!r}")


@dataclass
class BundleRow:
    line_index: int
    index: int
    name: str
    crc32: int
    size: int
    fields: list[str]


@dataclass
class ManifestInfo:
    version: int
    lines: list[str]
    newline: str
    had_final_newline: bool
    bundle_rows: dict[int, BundleRow]
    project_bundle_indices: list[int]


def parse_manifest(data: bytes) -> ManifestInfo:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PatchAbort(f"gameres is not valid UTF-8: {e}")

    newline = "\r\n" if "\r\n" in text else "\n"
    had_final_newline = text.endswith(("\n", "\r\n"))
    lines = text.splitlines()

    version = None
    for i, line in enumerate(lines):
        if line == "[Version]" and i + 1 < len(lines):
            try:
                version = int(lines[i + 1].split(",", 1)[0])
            except Exception:
                pass
            break
    if version is None:
        raise PatchAbort("Could not parse [Version] from gameres.")

    bundle_rows: dict[int, BundleRow] = {}
    in_bundles = False
    for i, line in enumerate(lines):
        if line == "[Bundles]":
            in_bundles = True
            continue
        if in_bundles and line.startswith("[") and line.endswith("]"):
            in_bundles = False
        if not in_bundles or not line or line.startswith("#"):
            continue

        parts = line.split(",")
        if len(parts) < 8:
            continue
        try:
            idx = int(parts[0])
            crc = int(parts[2])
            size = int(parts[3])
        except ValueError:
            continue
        bundle_rows[idx] = BundleRow(
            line_index=i,
            index=idx,
            name=parts[1],
            crc32=crc,
            size=size,
            fields=parts,
        )

    # Asset mapping rows look like:
    #   40468,7532/ChatNetManager.bytes,1163
    project_indices = set()
    rx = re.compile(r"^\d+,[^,]*/ChatNetManager\.bytes,(\d+)$")
    for line in lines:
        m = rx.match(line)
        if m:
            project_indices.add(int(m.group(1)))

    # Deliberately do NOT match ChatNetManagerBase.bytes.
    project_bundle_indices = sorted(project_indices)

    if not bundle_rows:
        raise PatchAbort("No [Bundles] rows found in gameres.")
    if not project_bundle_indices:
        raise PatchAbort(
            "Could not find any */ChatNetManager.bytes asset mapping in gameres. "
            "The game layout may have changed."
        )

    missing = [x for x in project_bundle_indices if x not in bundle_rows]
    if missing:
        raise PatchAbort(
            f"ChatNetManager asset mappings point to unknown bundle indices: {missing}"
        )

    return ManifestInfo(
        version=version,
        lines=lines,
        newline=newline,
        had_final_newline=had_final_newline,
        bundle_rows=bundle_rows,
        project_bundle_indices=project_bundle_indices,
    )


def split_wrapper(data: bytes) -> tuple[bytes, bytes]:
    if data.startswith(UNITYRAW + UNITYFS):
        return UNITYRAW, data[len(UNITYRAW):]
    if data.startswith(UNITYFS):
        return b"", data
    raise PatchAbort(
        "Expected UnityRawUnityFS or UnityFS bundle, got: "
        + data[:24].hex(" ")
    )


def raw_script(asset) -> bytes:
    value = asset.m_Script
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape")
    return bytes(value)


def to_stock_lua(data: bytes) -> bytes:
    if data.startswith(CUSTOM_LUA_PREFIX):
        return data[:4] + data[5:]
    if data.startswith(STOCK_LUA_PREFIX):
        return data
    raise PatchAbort(
        "Unexpected Lua header: " + data[:24].hex(" ")
    )


def to_lastz_lua(data: bytes) -> bytes:
    if data.startswith(CUSTOM_LUA_PREFIX):
        return data
    if data.startswith(STOCK_LUA_PREFIX):
        return data[:4] + b"\x03" + data[4:]
    raise PatchAbort(
        "Unexpected patched Lua header: " + data[:24].hex(" ")
    )


def small_lua_string_record(text: bytes) -> bytes:
    # Lua 5.4 dumpString stores len(text)+1, and for values <128 the
    # varint is one byte: 0x80 | size.
    size = len(text) + 1
    if size >= 0x80:
        raise PatchAbort("Internal error: short string helper got a long string.")
    return bytes([0x80 | size]) + text


@dataclass
class TargetInfo:
    path_id: int
    original_custom: bytes
    original_stock: bytes
    asset_name: str


def find_active_chatnet(env) -> TargetInfo:
    matches = []
    all_chatnets = []

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        asset = obj.read()
        if getattr(asset, "m_Name", "") != "ChatNetManager":
            continue

        data = raw_script(asset)
        stock = to_stock_lua(data)
        all_chatnets.append((int(obj.path_id), len(data)))

        if CALLBACK_MARKER in stock and WEBSOCKET_MARKER in stock:
            matches.append(
                TargetInfo(
                    path_id=int(obj.path_id),
                    original_custom=data,
                    original_stock=stock,
                    asset_name="ChatNetManager",
                )
            )

    if len(matches) != 1:
        raise PatchAbort(
            "Expected exactly one active project ChatNetManager containing both "
            f"{CALLBACK_MARKER!r} and {WEBSOCKET_MARKER!r}; found {len(matches)}.\n"
            f"All ChatNetManager TextAssets: {all_chatnets}\n"
            "A game update may have changed the structure."
        )
    return matches[0]


@dataclass
class LuaPatchResult:
    patched_stock: bytes
    chatprint_hits: int
    move_offset: int
    already_patched: bool


def patch_chatnet_stock(stock: bytes) -> LuaPatchResult:
    if not stock.startswith(STOCK_LUA_PREFIX):
        raise PatchAbort("Target is not normalized stock Lua 5.4.")

    marker_positions = []
    p = 0
    while True:
        p = stock.find(CALLBACK_MARKER, p)
        if p < 0:
            break
        marker_positions.append(p)
        p += 1

    if len(marker_positions) != 1:
        raise PatchAbort(
            f"Expected exactly one callback marker, found {len(marker_positions)}."
        )

    old_print = small_lua_string_record(b"ChatPrint")
    new_print = small_lua_string_record(b"print")
    old_hits = stock.count(old_print)
    new_hits_before = stock.count(new_print)

    # Already patched by our known method?
    marker = marker_positions[0]
    window_start = max(0, marker - 1024)
    before_region = stock[window_start:marker]
    old_moves_before = [
        window_start + m.start()
        for m in re.finditer(re.escape(MOVE_7_1), before_region)
    ]
    new_moves_before = [
        window_start + m.start()
        for m in re.finditer(re.escape(MOVE_7_2), before_region)
    ]

    # Already patched by our known method?
    #
    # There may be OTHER legitimate MOVE 7 2 instructions in the same
    # 1 KiB byte window. Therefore we must not require exactly one raw
    # MOVE_7_2 byte sequence here. The previous version did that and could
    # produce a false SAFE STOP even though the intended callback operand
    # had been patched correctly.
    if old_hits == 0 and len(old_moves_before) == 0 and len(new_moves_before) >= 1:
        if new_hits_before < 1:
            raise PatchAbort(
                "MOVE operand looks patched, but no encoded 'print' constants were found. "
                "Refusing to guess."
            )
        return LuaPatchResult(
            patched_stock=stock,
            chatprint_hits=0,
            # Diagnostic only. Multiple legitimate MOVE 7 2 instructions may
            # exist in this window, so this is not used to modify anything.
            move_offset=new_moves_before[-1],
            already_patched=True,
        )

    if old_hits < 1:
        raise PatchAbort(
            "No encoded ChatPrint constant was found. "
            "The logger implementation may have changed."
        )

    # Known working transformation: replace all exact encoded ChatPrint constants.
    patched = stock.replace(old_print, new_print)

    marker_positions2 = []
    p = 0
    while True:
        p = patched.find(CALLBACK_MARKER, p)
        if p < 0:
            break
        marker_positions2.append(p)
        p += 1
    if len(marker_positions2) != 1:
        raise PatchAbort("Callback marker count changed unexpectedly after string patch.")

    marker2 = marker_positions2[0]
    start2 = max(0, marker2 - 1024)
    region2 = patched[start2:marker2]

    old_moves = [
        start2 + m.start()
        for m in re.finditer(re.escape(MOVE_7_1), region2)
    ]
    new_moves = [
        start2 + m.start()
        for m in re.finditer(re.escape(MOVE_7_2), region2)
    ]

    if len(old_moves) != 1:
        raise PatchAbort(
            "Known callback bytecode signature changed.\n"
            f"Expected exactly one MOVE 7 1 before callback marker; got {len(old_moves)} "
            f"at {[hex(x) for x in old_moves]}.\n"
            f"Existing MOVE 7 2 hits: {[hex(x) for x in new_moves]}\n"
            "STOPPED SAFELY: this game version needs manual review."
        )

    off = old_moves[0]
    buf = bytearray(patched)
    buf[off:off + 4] = MOVE_7_2
    patched = bytes(buf)

    # Final structural verification.
    #
    # Do NOT require there to be exactly one MOVE 7 2 in the whole window.
    # The surrounding Lua function can legitimately contain another identical
    # raw instruction. What matters is:
    #   1) the exact offset selected above changed from MOVE 7 1 -> MOVE 7 2,
    #   2) the old-pattern count dropped by exactly one,
    #   3) the new-pattern count increased by exactly one.
    marker3 = patched.find(CALLBACK_MARKER)
    start3 = max(0, marker3 - 1024)
    region3 = patched[start3:marker3]

    old_moves_after = [
        start3 + m.start()
        for m in re.finditer(re.escape(MOVE_7_1), region3)
    ]
    new_moves_after = [
        start3 + m.start()
        for m in re.finditer(re.escape(MOVE_7_2), region3)
    ]

    if patched[off:off + 4] != MOVE_7_2:
        raise PatchAbort(
            "Callback operand verification failed at the selected patch offset."
        )

    if len(old_moves_after) != len(old_moves) - 1:
        raise PatchAbort(
            "Unexpected MOVE 7 1 count after patch: "
            f"before={len(old_moves)}, after={len(old_moves_after)}."
        )

    if len(new_moves_after) != len(new_moves) + 1:
        raise PatchAbort(
            "Unexpected MOVE 7 2 count after patch: "
            f"before={len(new_moves)}, after={len(new_moves_after)}."
        )

    print(
        "[PATCH] callback MOVE verification: "
        f"MOVE 7 1 {len(old_moves)} -> {len(old_moves_after)}, "
        f"MOVE 7 2 {len(new_moves)} -> {len(new_moves_after)}, "
        f"patched offset=0x{off:x}"
    )

    if patched.count(old_print) != 0:
        raise PatchAbort("Encoded ChatPrint records remain after patch.")
    if patched.count(new_print) < old_hits:
        raise PatchAbort("Unexpected encoded print record count after patch.")

    return LuaPatchResult(
        patched_stock=patched,
        chatprint_hits=old_hits,
        move_offset=off,
        already_patched=False,
    )


def repack_bundle(UnityPy, original_bundle: bytes, target: TargetInfo, patched_stock: bytes) -> bytes:
    wrapper, inner = split_wrapper(original_bundle)
    env = UnityPy.load(inner)

    matched = []
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        if int(obj.path_id) != target.path_id:
            continue
        asset = obj.read()
        if getattr(asset, "m_Name", "") == target.asset_name:
            matched.append((obj, asset))

    if len(matched) != 1:
        raise PatchAbort(
            f"Could not uniquely re-find {target.asset_name} path_id={target.path_id}."
        )

    obj, asset = matched[0]
    before = raw_script(asset)
    if before != target.original_custom:
        raise PatchAbort("Target TextAsset changed between discovery and repack.")

    replacement = to_lastz_lua(patched_stock)
    asset.m_Script = replacement.decode("utf-8", "surrogateescape")
    asset.save()

    saved_inner = env.file.save()
    patched_bundle = wrapper + saved_inner

    # Exact round-trip verification.
    w2, i2 = split_wrapper(patched_bundle)
    env2 = UnityPy.load(i2)
    after = None
    for o in env2.objects:
        if o.type.name != "TextAsset" or int(o.path_id) != target.path_id:
            continue
        a = o.read()
        if getattr(a, "m_Name", "") == target.asset_name:
            after = raw_script(a)
            break

    if after is None:
        raise PatchAbort("Patched TextAsset disappeared after bundle serialization.")
    if after != replacement:
        raise PatchAbort(
            "UnityPy round-trip verification FAILED: saved TextAsset bytes differ."
        )

    return patched_bundle


def patch_manifest(
    original_manifest: bytes,
    info: ManifestInfo,
    bundle_row: BundleRow,
    original_bundle: bytes,
    patched_bundle: bytes,
) -> bytes:
    if len(original_bundle) != bundle_row.size:
        raise PatchAbort(
            f"Manifest bundle size mismatch for {bundle_row.name}: "
            f"manifest={bundle_row.size}, file={len(original_bundle)}"
        )
    original_crc = crc32_u32(original_bundle)
    if original_crc != bundle_row.crc32:
        raise PatchAbort(
            f"Manifest CRC mismatch for {bundle_row.name}: "
            f"manifest={bundle_row.crc32}, file={original_crc}.\n"
            "Wrong/currently-changing bundle or manifest; refusing to patch."
        )

    new_fields = list(bundle_row.fields)
    new_fields[2] = str(crc32_u32(patched_bundle))
    new_fields[3] = str(len(patched_bundle))

    lines = list(info.lines)
    lines[bundle_row.line_index] = ",".join(new_fields)

    text = info.newline.join(lines)
    if info.had_final_newline:
        text += info.newline
    out = text.encode("utf-8")

    # Reparse and verify only target CRC/size are intended to differ in that row.
    info2 = parse_manifest(out)
    row2 = info2.bundle_rows[bundle_row.index]
    if row2.name != bundle_row.name:
        raise PatchAbort("Manifest verification failed: bundle name changed.")
    if row2.crc32 != crc32_u32(patched_bundle):
        raise PatchAbort("Manifest verification failed: patched CRC not present.")
    if row2.size != len(patched_bundle):
        raise PatchAbort("Manifest verification failed: patched size not present.")
    if info2.version != info.version:
        raise PatchAbort("Manifest verification failed: resource version changed.")

    return out


def write_sidecar(manifest: bytes, version: int) -> bytes:
    return f"{version},{len(manifest)},{crc32_u32(manifest)}".encode("ascii")


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class DeviceSnapshot:
    run_dir: Path
    manifest_path: Path
    version_path: Path
    manifest: bytes
    sidecar_text: str
    manifest_info: ManifestInfo
    bundle_row: BundleRow
    bundle_path: Path
    bundle: bytes
    target: TargetInfo
    app_info: dict


def snapshot_device(args, UnityPy, *, label: str) -> DeviceSnapshot:
    run_dir = Path(args.workspace) / "backups" / f"{now_stamp()}_{label}"
    run_dir.mkdir(parents=True, exist_ok=False)

    base = args.base.rstrip("/")
    remote_manifest = f"{base}/files/AssetBundles/gameres"
    remote_version = f"{base}/files/AssetBundles/gameres.version"

    manifest_path = run_dir / "gameres.original"
    version_path = run_dir / "gameres.version.original"

    pull(args.adb, args.device, remote_manifest, manifest_path)

    sidecar = remote_cat(args.adb, args.device, remote_version)
    if sidecar is None:
        raise PatchAbort(f"Could not read {remote_version}")
    version_path.write_text(sidecar, encoding="ascii")

    manifest = manifest_path.read_bytes()
    info = parse_manifest(manifest)
    side_version, side_size, side_crc = parse_sidecar(sidecar)

    actual_manifest_size = len(manifest)
    actual_manifest_crc = crc32_u32(manifest)

    # Important Last Z quirk:
    # after startup the game may rewrite gameres.version back to the package/
    # original sidecar values while leaving the active external gameres and
    # patched project bundle untouched. Therefore size/CRC mismatch in the
    # sidecar is NOT sufficient evidence that the patch disappeared.
    #
    # Resource version mismatch is still treated as unsafe because that can
    # indicate an official resource update or a half-finished updater state.
    if side_version != info.version:
        raise PatchAbort(
            f"gameres.version says version {side_version}, but gameres [Version] "
            f"says {info.version}. The resource version itself differs, so the "
            "updater refuses to guess."
        )

    sidecar_matches_manifest = (
        side_size == actual_manifest_size
        and side_crc == actual_manifest_crc
    )

    if not sidecar_matches_manifest:
        print(
            "[WARN] gameres.version does not match the active external gameres."
        )
        print(
            f"       sidecar: version={side_version} size={side_size} crc={side_crc}"
        )
        print(
            f"       gameres: version={info.version} size={actual_manifest_size} "
            f"crc={actual_manifest_crc}"
        )
        print(
            "       Continuing because Last Z can restore only the sidecar after "
            "startup while keeping the active manifest/bundle patched."
        )
        print(
            "       The project bundle will still be verified against the actual "
            "gameres row before status/apply continues."
        )

    if len(info.project_bundle_indices) != 1:
        raise PatchAbort(
            "Expected one unique project bundle index for */ChatNetManager.bytes; "
            f"found {info.project_bundle_indices}. STOPPED SAFELY."
        )

    bundle_index = info.project_bundle_indices[0]
    row = info.bundle_rows[bundle_index]

    bundle_path = run_dir / row.name
    remote_bundle = f"{base}/files/AssetBundles/{row.name}"
    pull(args.adb, args.device, remote_bundle, bundle_path)
    bundle = bundle_path.read_bytes()

    if len(bundle) != row.size or crc32_u32(bundle) != row.crc32:
        raise PatchAbort(
            "Pulled project bundle does not match current gameres row.\n"
            f"row: size={row.size} crc={row.crc32}\n"
            f"file: size={len(bundle)} crc={crc32_u32(bundle)}\n"
            "The updater may still be changing files. Let the game finish updating first."
        )

    wrapper, inner = split_wrapper(bundle)
    env = UnityPy.load(inner)
    target = find_active_chatnet(env)

    app_info = get_package_versions(args.adb, args.device, args.package)

    metadata = {
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "resource_version": info.version,
        "sidecar": sidecar,
        "sidecar_matches_manifest": sidecar_matches_manifest,
        "manifest": {
            "bytes": len(manifest),
            "crc32": crc32_u32(manifest),
            "sha256": sha256_bytes(manifest),
        },
        "project_bundle": {
            "index": row.index,
            "name": row.name,
            "bytes": len(bundle),
            "crc32": crc32_u32(bundle),
            "sha256": sha256_bytes(bundle),
        },
        "target_chatnet": {
            "path_id": target.path_id,
            "bytes": len(target.original_custom),
            "sha256": sha256_bytes(target.original_custom),
        },
        "app": app_info,
    }
    write_json(run_dir / "metadata.json", metadata)

    return DeviceSnapshot(
        run_dir=run_dir,
        manifest_path=manifest_path,
        version_path=version_path,
        manifest=manifest,
        sidecar_text=sidecar,
        manifest_info=info,
        bundle_row=row,
        bundle_path=bundle_path,
        bundle=bundle,
        target=target,
        app_info=app_info,
    )


def report_snapshot(s: DeviceSnapshot, patch_result: Optional[LuaPatchResult] = None) -> None:
    info = s.manifest_info
    row = s.bundle_row
    print("\n=== CURRENT LAST Z CHAT PATCH TARGET ===")
    print(f"App version       : {s.app_info or 'unknown'}")
    print(f"Resource version  : {info.version}")
    print(f"gameres bytes     : {len(s.manifest)}")
    print(f"gameres CRC32     : {crc32_u32(s.manifest)}")
    print(f"Bundle index      : {row.index}")
    print(f"Bundle name       : {row.name}")
    print(f"Bundle bytes      : {len(s.bundle)}")
    print(f"Bundle CRC32      : {crc32_u32(s.bundle)}")
    print(f"ChatNet path_id   : {s.target.path_id}")
    print(f"ChatNet bytes     : {len(s.target.original_custom)}")
    print(f"Backup            : {s.run_dir}")
    if patch_result is not None:
        print(f"Already patched   : {patch_result.already_patched}")
        print(f"ChatPrint hits    : {patch_result.chatprint_hits}")
        print(f"Callback MOVE off : 0x{patch_result.move_offset:x}")


def build_patch(args, UnityPy, s: DeviceSnapshot) -> tuple[Path, Path, Path, LuaPatchResult]:
    print("\n[PATCH] Verifying known ChatNetManager structure...")
    result = patch_chatnet_stock(s.target.original_stock)
    report_snapshot(s, result)

    build_dir = s.run_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    stock_out = build_dir / f"ChatNetManager_{s.target.path_id}.payload.stock.luac"
    stock_out.write_bytes(result.patched_stock)

    if result.already_patched:
        print("\n[PATCH] Current project bundle is already patched by the known signature.")
        patched_bundle = s.bundle
    else:
        print("\n[PATCH] Repacking project bundle...")
        patched_bundle = repack_bundle(
            UnityPy, s.bundle, s.target, result.patched_stock
        )

    patched_bundle_path = build_dir / s.bundle_row.name
    patched_bundle_path.write_bytes(patched_bundle)

    if (
        len(patched_bundle) == len(s.bundle)
        and sha256_bytes(patched_bundle) == sha256_bytes(s.bundle)
        and not result.already_patched
    ):
        raise PatchAbort("Patch unexpectedly produced an unchanged bundle.")

    # If already patched, current manifest should already match it. Otherwise patch row.
    if result.already_patched:
        patched_manifest = s.manifest
    else:
        print("[PATCH] Updating gameres bundle CRC32 + size...")
        patched_manifest = patch_manifest(
            s.manifest,
            s.manifest_info,
            s.bundle_row,
            s.bundle,
            patched_bundle,
        )

    patched_manifest_path = build_dir / "gameres"
    patched_manifest_path.write_bytes(patched_manifest)

    sidecar = write_sidecar(patched_manifest, s.manifest_info.version)
    patched_version_path = build_dir / "gameres.version"
    patched_version_path.write_bytes(sidecar)

    # Final local verification.
    info2 = parse_manifest(patched_manifest)
    row2 = info2.bundle_rows[s.bundle_row.index]
    if row2.size != len(patched_bundle):
        raise PatchAbort("Final manifest row size does not match patched bundle.")
    if row2.crc32 != crc32_u32(patched_bundle):
        raise PatchAbort("Final manifest row CRC32 does not match patched bundle.")
    v, sz, cr = parse_sidecar(sidecar.decode("ascii"))
    if v != info2.version or sz != len(patched_manifest) or cr != crc32_u32(patched_manifest):
        raise PatchAbort("Final gameres.version verification failed.")

    build_meta = {
        "resource_version": info2.version,
        "bundle_index": row2.index,
        "bundle_name": row2.name,
        "target_path_id": s.target.path_id,
        "already_patched_input": result.already_patched,
        "patched_bundle": {
            "bytes": len(patched_bundle),
            "crc32": crc32_u32(patched_bundle),
            "sha256": sha256_bytes(patched_bundle),
        },
        "patched_manifest": {
            "bytes": len(patched_manifest),
            "crc32": crc32_u32(patched_manifest),
            "sha256": sha256_bytes(patched_manifest),
        },
        "sidecar": sidecar.decode("ascii"),
        "lua": {
            "chatprint_records_replaced": result.chatprint_hits,
            "callback_move_offset": result.move_offset,
            "sha256": sha256_bytes(result.patched_stock),
        },
    }
    write_json(build_dir / "build_metadata.json", build_meta)

    print("\n=== BUILD OK ===")
    print(f"Patched bundle   : {patched_bundle_path}")
    print(f"  size           : {len(patched_bundle)}")
    print(f"  crc32          : {crc32_u32(patched_bundle)}")
    print(f"  sha256         : {sha256_bytes(patched_bundle)}")
    print(f"Patched gameres  : {patched_manifest_path}")
    print(f"  size           : {len(patched_manifest)}")
    print(f"  crc32          : {crc32_u32(patched_manifest)}")
    print(f"gameres.version  : {sidecar.decode('ascii')}")
    print(f"Backup + build   : {s.run_dir}")

    return patched_bundle_path, patched_manifest_path, patched_version_path, result


def deploy_patch(args, s: DeviceSnapshot, patched_bundle: Path, patched_manifest: Path, patched_version: Path) -> None:
    base = args.base.rstrip("/")
    row = s.bundle_row

    remote_builtin_dir = f"{base}/cache/Builtin"
    remote_assets_dir = f"{base}/files/AssetBundles"

    print("\n[DEPLOY] Force-stopping Last Z...")
    adb_shell(["am", "force-stop", args.package], args.adb, args.device)

    adb_shell(["mkdir", "-p", remote_builtin_dir], args.adb, args.device)
    adb_shell(["mkdir", "-p", remote_assets_dir], args.adb, args.device)

    print("[DEPLOY] Pushing patched manifest + sidecar to Builtin bootstrap location...")
    push(args.adb, args.device, patched_manifest, f"{remote_builtin_dir}/gameres")
    push(args.adb, args.device, patched_version, f"{remote_builtin_dir}/gameres.version")

    print("[DEPLOY] Pushing patched manifest + sidecar to active AssetBundles...")
    push(args.adb, args.device, patched_manifest, f"{remote_assets_dir}/gameres")
    push(args.adb, args.device, patched_version, f"{remote_assets_dir}/gameres.version")

    print("[DEPLOY] Pushing patched project ChatNetManager bundle...")
    push(args.adb, args.device, patched_bundle, f"{remote_assets_dir}/{row.name}")

    expected_manifest_sha = sha256_bytes(patched_manifest.read_bytes())
    expected_bundle_sha = sha256_bytes(patched_bundle.read_bytes())
    expected_sidecar = patched_version.read_text(encoding="ascii")

    print("\n[VERIFY PRE-START]")
    for label, remote, expected in [
        ("Builtin gameres", f"{remote_builtin_dir}/gameres", expected_manifest_sha),
        ("Active gameres", f"{remote_assets_dir}/gameres", expected_manifest_sha),
        ("Project bundle", f"{remote_assets_dir}/{row.name}", expected_bundle_sha),
    ]:
        actual = remote_sha256(args.adb, args.device, remote)
        print(f"{label:18}: {actual}")
        if actual != expected:
            raise PatchAbort(
                f"Remote SHA256 verification failed for {remote}: "
                f"expected {expected}, got {actual}"
            )

    bver = remote_cat(args.adb, args.device, f"{remote_builtin_dir}/gameres.version")
    aver = remote_cat(args.adb, args.device, f"{remote_assets_dir}/gameres.version")
    print(f"Builtin version    : {bver}")
    print(f"Active version     : {aver}")
    if bver != expected_sidecar or aver != expected_sidecar:
        raise PatchAbort("Remote gameres.version verification failed.")

    print("\n[OK] Patch deployed and verified before game start.")

    if args.start:
        print("[START] Starting Last Z...")
        adb_shell(
            ["monkey", "-p", args.package, "-c", "android.intent.category.LAUNCHER", "1"],
            args.adb,
            args.device,
        )
        print(
            "\n[STARTED] After the game reaches the map, run:\n"
            "  py update_patch.py status\n"
            "to verify that the active manifest/bundle were not replaced."
        )


def cmd_status(args) -> None:
    UnityPy = require_unitypy()
    require_adb(args.adb, args.device)
    s = snapshot_device(args, UnityPy, label="status")
    result = patch_chatnet_stock(s.target.original_stock)
    report_snapshot(s, result)

    active_ver = remote_cat(
        args.adb, args.device, f"{args.base.rstrip('/')}/files/AssetBundles/gameres.version"
    )
    builtin_ver = remote_cat(
        args.adb, args.device, f"{args.base.rstrip('/')}/cache/Builtin/gameres.version"
    )
    print("\nDevice sidecars:")
    print(f"  active : {active_ver}")
    print(f"  builtin: {builtin_ver}")

    if result.already_patched:
        print("\n[STATUS] ACTIVE PROJECT CHAT BUNDLE LOOKS PATCHED.")
    else:
        print("\n[STATUS] ACTIVE PROJECT CHAT BUNDLE IS NOT PATCHED YET.")
        print("Patch can be installed using the PEva.cmd launcher after compatibility verification.")

    # Inspect all relevant *distinct* resource locations. In particular the
    # application's Download cache must not be silently conflated with the
    # AssetBundles directory whose chat bundle was actually inspected above.
    # These are observations only: no write or guessed cache location is used.
    base = args.base.rstrip("/")
    name = s.bundle_row.name
    paths = {
        "asset_manifest": f"{base}/files/AssetBundles/gameres",
        "asset_sidecar": f"{base}/files/AssetBundles/gameres.version",
        "asset_bundle": f"{base}/files/AssetBundles/{name}",
        "builtin_manifest": f"{base}/cache/Builtin/gameres",
        "builtin_sidecar": f"{base}/cache/Builtin/gameres.version",
        "download_manifest": f"{base}/cache/Download/gameres",
        "download_sidecar": f"{base}/cache/Download/gameres.version",
        "download_bundle": f"{base}/cache/Download/{name}",
    }
    remote_digests = {k: remote_sha256(args.adb, args.device, v) for k, v in paths.items()}
    summary = {
        "patch_present_in_asset_bundle": bool(result.already_patched),
        "package_version": s.app_info,
        "resource_version": s.manifest_info.version,
        "bundle_name": name,
        "bundle_path_id": s.target.path_id,
        "asset_manifest_sha256_local": sha256_bytes(s.manifest),
        "asset_bundle_sha256_local": sha256_bytes(s.bundle),
        "locations": {k: {"path": paths[k], "sha256": remote_digests[k]} for k in paths},
        "snapshot_backup": str(s.run_dir),
        "scope_warning": "AssetBundles signature on disk is not proof Lua was executed",
    }
    print("PEVA_STATUS_JSON=" + json.dumps(summary, ensure_ascii=True, separators=(",", ":")))


def cmd_apply(args) -> None:
    UnityPy = require_unitypy()
    require_adb(args.adb, args.device)

    # Freeze files before snapshot/build so updater cannot change them mid-run.
    print("[PREP] Force-stopping Last Z to freeze resource files...")
    adb_shell(["am", "force-stop", args.package], args.adb, args.device)

    s = snapshot_device(args, UnityPy, label="apply")
    patched_bundle, patched_manifest, patched_version, result = build_patch(args, UnityPy, s)

    if args.build_only:
        print("\n[BUILD-ONLY] Nothing was pushed to BlueStacks.")
        return

    deploy_patch(args, s, patched_bundle, patched_manifest, patched_version)


def cmd_restore(args) -> None:
    require_adb(args.adb, args.device)
    backup = Path(args.from_backup)
    meta_path = backup / "metadata.json"
    if not meta_path.exists():
        raise PatchAbort(f"metadata.json not found in backup: {backup}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    manifest = backup / "gameres.original"
    version = backup / "gameres.version.original"
    bundle_name = meta["project_bundle"]["name"]
    bundle = backup / bundle_name

    if not (manifest.exists() and version.exists() and bundle.exists()):
        raise PatchAbort("Backup is incomplete; restore aborted.")

    base = args.base.rstrip("/")
    bdir = f"{base}/cache/Builtin"
    adir = f"{base}/files/AssetBundles"

    adb_shell(["am", "force-stop", args.package], args.adb, args.device)
    push(args.adb, args.device, manifest, f"{bdir}/gameres")
    push(args.adb, args.device, version, f"{bdir}/gameres.version")
    push(args.adb, args.device, manifest, f"{adir}/gameres")
    push(args.adb, args.device, version, f"{adir}/gameres.version")
    push(args.adb, args.device, bundle, f"{adir}/{bundle_name}")

    print("[OK] Original files from that backup were restored.")
    if args.start:
        adb_shell(
            ["monkey", "-p", args.package, "-c", "android.intent.category.LAUNCHER", "1"],
            args.adb,
            args.device,
        )


def add_common(ap):
    ap.add_argument("--adb", default=DEFAULT_ADB)
    ap.add_argument("--device", default=DEFAULT_DEVICE)
    ap.add_argument("--package", default=DEFAULT_PACKAGE)
    ap.add_argument("--activity", default=DEFAULT_ACTIVITY)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--workspace", default=DEFAULT_WORKSPACE)


def make_parser():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="Inspect current game resources and patch compatibility.")
    add_common(s)
    s.set_defaults(func=cmd_status)

    a = sub.add_parser("apply", help="Build + deploy patch for the CURRENT installed resource version.")
    add_common(a)
    a.add_argument("--build-only", action="store_true", help="Build and verify locally, do not push.")
    a.add_argument("--start", action="store_true", help="Start Last Z after successful deployment.")
    a.set_defaults(func=cmd_apply)

    r = sub.add_parser("restore", help="Restore originals from a backup created by this script.")
    add_common(r)
    r.add_argument("--from-backup", required=True, help="Backup directory created by status/apply.")
    r.add_argument("--start", action="store_true")
    r.set_defaults(func=cmd_restore)

    return ap


def main():
    parser = make_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n[ABORT] Interrupted by user.")
        raise SystemExit(130)
    except PatchAbort as e:
        print("\n" + "=" * 78)
        print("[SAFE STOP]")
        print(str(e))
        print("=" * 78)
        print(
            "No unknown/new bytecode was patched after this check failed.\n"
            "If this happened after a game update, send me the console output and "
            "the newest backup metadata/logs for review."
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
