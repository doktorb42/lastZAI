import cv2

from archive.legacy_runtime.adb_controll import screenshot


screen_path = screenshot("calibration.png")

image = cv2.imread(str(screen_path))

if image is None:
    raise RuntimeError("Cannot load screenshot")


display = image.copy()


def mouse_callback(event, x, y, flags, param):
    global display

    if event == cv2.EVENT_LBUTTONDOWN:
        print()
        print("=" * 30)
        print(f"CLICK")
        print(f"x = {x}")
        print(f"y = {y}")
        print("=" * 30)

        display = image.copy()

        cv2.circle(
            display,
            (x, y),
            8,
            (0, 0, 255),
            2
        )

        cv2.line(
            display,
            (x - 20, y),
            (x + 20, y),
            (0, 0, 255),
            1
        )

        cv2.line(
            display,
            (x, y - 20),
            (x, y + 20),
            (0, 0, 255),
            1
        )

        cv2.putText(
            display,
            f"{x}, {y}",
            (x + 15, y - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2
        )


cv2.namedWindow(
    "Last Z Coordinate Calibration",
    cv2.WINDOW_NORMAL
)

cv2.resizeWindow(
    "Last Z Coordinate Calibration",
    1280,
    720
)

cv2.setMouseCallback(
    "Last Z Coordinate Calibration",
    mouse_callback
)

print("Last Z coordinate calibration")
print("Resolution: 1600x900")
print()
print("Click an element to get its coordinates.")
print("Press R = refresh screenshot")
print("Press Q = quit")


while True:

    cv2.imshow(
        "Last Z Coordinate Calibration",
        display
    )

    key = cv2.waitKey(20) & 0xFF

    if key == ord("q"):
        break

    if key == ord("r"):
        screen_path = screenshot("calibration.png")

        image = cv2.imread(str(screen_path))
        display = image.copy()

        print("[REFRESH]")


cv2.destroyAllWindows()
