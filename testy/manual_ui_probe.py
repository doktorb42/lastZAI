"""PEva: read-only Android UI hierarchy tester for Last Z on BlueStacks.
No APK/Lua changes, no model, no game input, no CUDA.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADB_LOCATIONS = [Path(r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe")]
SERIAL = "emulator-5554"
GAME_PACKAGE = "com.readygo.barrel.gp"
SCREENS = [
    ("world_map", "Otworz MAPE SWIATA Last Z w BlueStacks."),
    ("alliance_chat", "Otworz CZAT SOJUSZU tak, aby byly widoczne wiadomosci."),
    ("private_chat", "Otworz KONKRETNA ROZMOWE PRYWATNA z widocznym tekstem."),
]


def find_adb() -> str:
    for item in ADB_LOCATIONS:
        if item.is_file():
            return str(item)
    return shutil.which("adb") or ""


def run(args: list[str], timeout: int = 30, **kwargs):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ADB nie odpowiedzial na czas (mozliwy problem z UIAutomator).") from exc


def collect_xml(adb: str, folder: Path, name: str) -> dict:
    remote = "/sdcard/peva_ui_dump.xml"
    local = folder / f"{name}.xml"
    dump = run([adb, "-s", SERIAL, "shell", "uiautomator", "dump", remote], timeout=35)
    output = (dump.stdout + dump.stderr).decode("utf-8", errors="replace").strip()
    if dump.returncode != 0:
        return {"screen": name, "result": "UI_DUMP_FAILED", "error": output[-1500:]}
    pulled = run([adb, "-s", SERIAL, "pull", remote, str(local)], timeout=30)
    if pulled.returncode != 0 or not local.exists():
        return {"screen": name, "result": "UI_PULL_FAILED", "error": (pulled.stdout + pulled.stderr).decode("utf-8", errors="replace")[-1500:]}
    try:
        tree = ET.parse(local)
    except ET.ParseError as exc:
        return {"screen": name, "result": "XML_INVALID", "error": str(exc)}

    game_nodes = 0
    all_nodes = 0
    all_texts = []
    game_texts = []
    other_texts = []
    for node in tree.iter():
        if node.tag != "node":
            continue
        all_nodes += 1
        package = node.attrib.get("package", "")
        text = (node.attrib.get("text", "") or node.attrib.get("content-desc", "")).strip()
        if package == GAME_PACKAGE:
            game_nodes += 1
        if text:
            entry = {"package": package, "text": text[:600], "bounds": node.attrib.get("bounds", "")}
            all_texts.append(entry)
            (game_texts if package == GAME_PACKAGE else other_texts).append(entry)
    result = {
        "screen": name, "result": "TEXT_IN_LASTZ_XML" if game_texts else "NO_GAME_TEXT_IN_XML",
        "all_nodes": all_nodes, "game_nodes": game_nodes, "all_text_nodes": len(all_texts),
        "game_text_nodes": len(game_texts), "game_texts": game_texts,
        "other_text_nodes": len(other_texts), "other_texts": other_texts[:12],
        "xml_path": local.name,
    }
    return result


def screenshot(adb: str, folder: Path, name: str) -> str:
    # Separate binary subprocess to avoid Windows CRLF corruption of PNG bytes.
    res = run([adb, "-s", SERIAL, "exec-out", "screencap", "-p"], timeout=30)
    if res.returncode == 0 and res.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        path = folder / f"{name}.png"
        path.write_bytes(res.stdout)
        return path.name
    return "SCREENSHOT_UNAVAILABLE"


def main() -> int:
    print("=" * 70)
    print("PEva | TEST 1/4 | Android UIAutomator (bez OCR i bez GPU)")
    print("=" * 70)
    adb = find_adb()
    if not adb:
        print("[ERROR] Brak ADB. Oczekiwana sciezka: C:\\Program Files\\BlueStacks_nxt\\HD-Adb.exe")
        return 1
    check = run([adb, "-s", SERIAL, "get-state"], timeout=12)
    if check.returncode != 0 or check.stdout.strip() != b"device":
        print("[ERROR] BlueStacks nie jest podlaczony jako emulator-5554.")
        print((check.stdout + check.stderr).decode("utf-8", errors="replace")[-1000:])
        return 1
    folder = ROOT / "data" / "test-artifacts" / ("peva_ui_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    folder.mkdir(parents=True, exist_ok=True)
    report = {"device": SERIAL, "package": GAME_PACKAGE, "method": "uiautomator", "gpu_model_required": False, "screens": []}
    for name, instruction in SCREENS:
        print("\n" + instruction)
        print("Nastepnie wroc do tego okna i wcisnij ENTER, aby przechwycic ekran.")
        try:
            input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\n[STOP] Przerwano test.")
            break
        print("[INFO] Czytam hierarchie Androida...")
        try:
            entry = collect_xml(adb, folder, name)
            entry["screenshot"] = screenshot(adb, folder, name)
        except Exception as exc:
            entry = {"screen": name, "result": "ERROR", "error": str(exc)}
        report["screens"].append(entry)
        print("[{}] {} | elementy gry={} | teksty gry={}".format(name.upper(), entry.get("result"), entry.get("game_nodes", "?"), entry.get("game_text_nodes", "?")))
        for item in entry.get("game_texts", [])[:6]:
            print("   Tekst: " + item["text"].replace("\n", " ")[:120])
        if entry.get("error"):
            print("   Blad: " + entry["error"][:200])

    report_path = folder / "raport.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nRaport zapisany: " + str(report_path))
    print("[UWAGA] Raport i zrzuty moga zawierac prywatne wiadomosci. Przejrzyj przed udostepnieniem.")
    if not any(s.get("game_text_nodes", 0) for s in report["screens"]):
        print("[WYNIK] Android nie udostepnil tekstu gry. Kolejny test: OCR CPU.")
    else:
        print("[WYNIK] Android ujawnia czesc tekstu. Trzeba sprawdzic, czy to TEKST CZATU, a nie sam interfejs.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("[ERROR] " + str(exc))
        raise SystemExit(1)
