# coordinates.py

from config.runtime_config import SCREEN_HEIGHT, SCREEN_WIDTH

# Verified after the post-cleanup APK installation.  The compact chat ticker
# accepts a tap in its visible text area; the previous y=1556 landed below it.
CHAT_BUTTON = (350, 1510)

ALLIANCE_CHAT_TAB = (452, 140)
PRIVATE_CHAT_TAB = (783, 138)

WRITE_CHAT = (176, 1522)
SEND_TEXT = (816, 1524)

BACK_BUTTON = (81, 1527)

PRIVATE_PERSON_X = 180
PRIVATE_PERSON_FIRST_Y = 450
PRIVATE_PERSON_STEP_Y = 150
PRIVATE_PERSON_VISIBLE_COUNT = 7
