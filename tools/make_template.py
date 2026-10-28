import cv2
from pathlib import Path

from archive.legacy_runtime.adb_controll import screenshot


TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates"
TEMPLATES.mkdir(exist_ok=True)

# 0.5 = podgląd 50%
DISPLAY_SCALE = 0.5


# =========================
# SCREENSHOT
# =========================

screen_path = screenshot("template_source.png")

original = cv2.imread(str(screen_path))

if original is None:
    raise RuntimeError("Cannot load screenshot")


original_height, original_width = original.shape[:2]

print()
print(f"Original screenshot: {original_width}x{original_height}")


# =========================
# SCALED PREVIEW
# =========================

display_width = int(original_width * DISPLAY_SCALE)
display_height = int(original_height * DISPLAY_SCALE)

display = cv2.resize(
    original,
    (display_width, display_height),
    interpolation=cv2.INTER_AREA
)

print(f"Preview: {display_width}x{display_height}")
print()


# =========================
# SELECT ROI
# =========================

selection = cv2.selectROI(
    "Select template",
    display,
    showCrosshair=True,
    fromCenter=False
)

display_x, display_y, display_w, display_h = selection

cv2.destroyAllWindows()


if display_w == 0 or display_h == 0:
    print("Cancelled")
    raise SystemExit


# =========================
# CONVERT BACK TO ORIGINAL
# =========================

x = int(display_x / DISPLAY_SCALE)
y = int(display_y / DISPLAY_SCALE)

w = int(display_w / DISPLAY_SCALE)
h = int(display_h / DISPLAY_SCALE)


# zabezpieczenie przed wyjściem poza obraz
x = max(0, min(x, original_width - 1))
y = max(0, min(y, original_height - 1))

w = min(w, original_width - x)
h = min(h, original_height - y)


print()
print("Preview coordinates:")
print(
    f"x={display_x}, y={display_y}, "
    f"w={display_w}, h={display_h}"
)

print()
print("Original coordinates:")
print(
    f"x={x}, y={y}, "
    f"w={w}, h={h}"
)


# =========================
# CROP ORIGINAL IMAGE
# =========================

crop = original[
    y:y + h,
    x:x + w
]


# =========================
# SAVE
# =========================

print()

name = input(
    "Template name "
    "(example private_chat/private_tab): "
).strip()

if not name:
    print("No name provided")
    raise SystemExit


output = TEMPLATES / f"{name}.png"

output.parent.mkdir(
    parents=True,
    exist_ok=True
)

cv2.imwrite(
    str(output),
    crop
)


print()
print("=" * 50)
print("Template saved")
print("=" * 50)
print(output)

print()
print(
    f"Original ROI: "
    f"x={x}, y={y}, w={w}, h={h}"
)
