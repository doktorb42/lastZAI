"""PEva: CPU-only OCR & screen-state pilot for Last Z on BlueStacks.

Does not patch the game, send input, start an AI model, or use CUDA.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Must be set before ONNX Runtime is imported.
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

# Heavy OCR packages are deliberately loaded only by the manually invoked
# pilot. Importing this file for compatibility or inspection stays lightweight.
cv2: Any | None = None
np: Any | None = None
RapidOCR: Any | None = None

ROOT = Path(__file__).resolve().parents[1]
DEVICE = os.environ.get("PEVA_DEVICE", "emulator-5554")
ADB = Path(os.environ.get("PEVA_ADB", r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"))
CAPTURE_TIMEOUT = 18
DURATION_SECONDS = 60


def load_ocr_dependencies() -> None:
    """Load CPU OCR dependencies only for an explicit manual test run."""

    global RapidOCR, cv2, np
    if cv2 is not None and np is not None and RapidOCR is not None:
        return
    try:
        import cv2 as imported_cv2
        import numpy as imported_np
        from rapidocr import RapidOCR as imported_rapid_ocr
    except ImportError as exc:
        raise RuntimeError(
            "Brakuje zależności ręcznego pilota OCR. Uruchom go tylko w środowisku OCR."
        ) from exc
    cv2 = imported_cv2
    np = imported_np
    RapidOCR = imported_rapid_ocr


def run_adb(*args: str, timeout: int = CAPTURE_TIMEOUT) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([str(ADB), "-s", DEVICE, *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=False)


def screenshot() -> np.ndarray:
    p = run_adb("exec-out", "screencap", "-p")
    if p.returncode != 0:
        raise RuntimeError(f"ADB screencap failed: {p.stderr.decode('utf-8', 'replace')[:400]}")
    if not p.stdout.startswith(b"\x89PNG"):
        raise RuntimeError("ADB did not return a PNG screenshot.")
    img = cv2.imdecode(np.frombuffer(p.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Cannot decode PNG returned by BlueStacks.")
    return img


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def unpack_ocr(result) -> list[dict]:
    """Support RapidOCR 3.x RapidOCROutput and older (items, elapsed) results."""
    blocks = []
    if hasattr(result, "txts"):
        txts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        boxes = getattr(result, "boxes", None)
        txts = [] if txts is None else txts
        scores = [] if scores is None else scores
        boxes = [] if boxes is None else boxes
        for i, txt in enumerate(txts):
            blocks.append({"text": str(txt), "confidence": float(scores[i]) if i < len(scores) else None,
                           "box": np.asarray(boxes[i]).tolist() if i < len(boxes) else None})
    else:
        items = result[0] if isinstance(result, tuple) else result
        for item in items or []:
            try:
                points, txt, score = item
                blocks.append({"text": str(txt), "confidence": float(score),
                               "box": np.asarray(points).tolist()})
            except (ValueError, TypeError):
                continue
    return blocks


def ocr_image(engine: RapidOCR, img: np.ndarray, *, body_only: bool = False) -> tuple[list[dict], float]:
    h, w = img.shape[:2]
    if body_only:
        # Includes message bubbles + nicknames, excludes bottom text input and tab bar.
        top, bottom = int(h * 0.115), int(h * 0.91)
        clipped = img[top:bottom, int(w * 0.02):int(w * 0.98)]
    else:
        clipped = img
    # Upscaling is on CPU; helps with very small Unity UI type.
    input_image = cv2.resize(clipped, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    t0 = time.perf_counter()
    output = unpack_ocr(engine(input_image))
    elapsed = time.perf_counter() - t0
    return [b for b in output if b["text"].strip()], elapsed


def header_ref(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.resize(img[:int(h * 0.108)], (450, 90), interpolation=cv2.INTER_AREA)


def screen_state(img: np.ndarray, refs: dict[str, np.ndarray]) -> tuple[str, dict[str, float]]:
    if not refs:
        return "UNKNOWN", {}
    observed = header_ref(img)
    # Compare static top-bar UI with screenshots collected on this very emulator.
    diffs = {name: float(np.mean(cv2.absdiff(observed, ref))) for name, ref in refs.items()}
    best = min(diffs, key=diffs.get)
    ordered = sorted(diffs.values())
    if diffs[best] > 30 or (len(ordered) > 1 and ordered[1] - ordered[0] < 1.8):
        return "UNKNOWN", diffs
    return best.upper(), diffs


def main() -> int:
    print("=" * 67)
    print("PEva | TEST OCR NA CPU | BRAK MODELU LLM | BEZ PATCHA")
    print("=" * 67)
    load_ocr_dependencies()
    if not ADB.is_file():
        print(f"[BLAD] Nie znaleziono HD-Adb.exe: {ADB}")
        return 1
    state = run_adb("get-state")
    if state.returncode != 0 or state.stdout.strip() != b"device":
        print(f"[BLAD] BlueStacks ({DEVICE}) nie odpowiada. Wlacz emulator i ADB.")
        return 1

    output_dir = ROOT / "data" / "test-artifacts" / ("peva_ocr_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Raport: {output_dir}")
    print("[INFO] OCR uruchamiany na CPU przez ONNX Runtime. CUDA pozostaje wylaczona.")
    engine = RapidOCR()
    references: dict[str, np.ndarray] = {}
    result: dict = {"device": DEVICE, "method": "rapidocr_cpu", "stages": [],
                    "live_new_ocr_blocks": [], "notes": "Zmiana OCR nie jest dowodem nowej wiadomosci bez kontroli recznej."}
    stages = [("world_map", "MAPE SWIATA"), ("alliance_chat", "CZAT SOJUSZU"),
              ("private_chat", "PRYWATNA ROZMOWE")]
    for key, label in stages:
        input(f"\nOtworz w Last Z {label}, a nastepnie nacisnij ENTER...")
        img = screenshot()
        cv2.imwrite(str(output_dir / (key + ".png")), img)
        references[key] = header_ref(img)
        blocks, elapsed = ocr_image(engine, img, body_only=(key != "world_map"))
        print(f"[{key}] {len(blocks)} blokow tekstu | OCR: {elapsed:.2f}s")
        for block in blocks[:32]:
            print("  ", block["text"].replace("\n", " ")[:140])
        result["stages"].append({"screen": key, "blocks": blocks, "seconds": round(elapsed, 2),
                                 "screenshot": key + ".png"})

    print("\n[TEST NOWYCH WIADOMOSCI]")
    print("Otworz CZAT SOJUSZU. Najlepiej popros innego gracza o wyslanie nowej wiadomosci.")
    input("Gdy jestes w czacie, nacisnij ENTER. Najpierw zbieram stan poczatkowy...")
    img = screenshot()
    baseline, _ = ocr_image(engine, img, body_only=True)
    seen = {normalize(b["text"]) for b in baseline if len(normalize(b["text"])) >= 3}
    print(f"[BASELINE] Zapisano {len(seen)} rozpoznanych fragmentow. Teraz wyslij NOWA wiadomosc.")
    start = time.monotonic()
    samples = 0
    while time.monotonic() - start < DURATION_SECONDS:
        img = screenshot()
        state_name, diffs = screen_state(img, references)
        blocks, elapsed = ocr_image(engine, img, body_only=True)
        samples += 1
        for block in blocks:
            val = normalize(block["text"])
            if len(val) < 3 or val in seen:
                continue
            seen.add(val)
            observation = {"text": block["text"], "confidence": block["confidence"],
                           "screen_guess": state_name, "elapsed_seconds": round(time.monotonic() - start, 1)}
            result["live_new_ocr_blocks"].append(observation)
            print(f"[NOWY BLOK OCR / {state_name}] {block['text'][:160]}")
        if samples % 4 == 0:
            print(f"[STATUS] probki={samples}, ekran={state_name}, OCR={elapsed:.2f}s, nowe_bloki={len(result['live_new_ocr_blocks'])}")
        time.sleep(2)
    result["samples"] = samples
    result["status"] = "OCR_CHANGE_DETECTED" if result["live_new_ocr_blocks"] else "NO_NEW_OCR_TEXT"
    result["warning"] = "OCR moze rozpoznac niepoprawne lub powtarzajace sie fragmenty; sprawdz wynik recznie."
    report_file = output_dir / "raport.json"
    report_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 67)
    print("[WYNIK]", result["status"])
    print("[RAPORT]", report_file)
    print("[PRYWATNOSC] Przejrzyj raport: moze zawierac nicki i prywatne rozmowy.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] Test przerwany przez uzytkownika.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\n[BLAD] {type(exc).__name__}: {exc}")
        raise SystemExit(1)
