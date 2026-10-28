import cv2
from config.runtime_config import TEMPLATES_DIR


ALLIANCE_UNREAD_POS = (524, 102)
# The only private-message badge that distinguishes PM from Group Chat is the
# one on the *lower* Private Chat sub-tab.  The upper global Private Chat tab
# lights up for both categories, so it is diagnostic-only and never a trigger.
# Measured on the active 900 x 1600 BlueStacks layout. This is diagnostic
# only; the actual detector below uses the bounded lower-tab area.
PRIVATE_UNREAD_POS = (442, 288)
CHAT_UNREAD_POS = (700, 1500)

UNREAD_SEARCH_RADIUS = 35
UNREAD_THRESHOLD = 0.82

# The unread-dot templates predate the current Last Z UI skin.  On the live
# client the dot can be visually correct but score just below the strict
# template threshold.  This fallback is deliberately limited to the small
# badge area of the Private Chat tab; it is not a general "find red things"
# detector.
REFERENCE_SCREEN_SIZE = (900, 1600)
UNREAD_BADGE_MIN_AREA = 100
UNREAD_BADGE_MAX_AREA = 700

# Inside the global Private Chat page, Last Z has a second, lower strip:
# Private Chat on the left, Group Chat on the right.  A badge on the left is
# the first unambiguous PM signal.  The generous Y range covers BlueStacks'
# different chrome sizes; its narrow X range excludes Group Chat's right-side
# badge and every global top-strip badge.
LOWER_PRIVATE_BADGE_X_MIN = 0.34
LOWER_PRIVATE_BADGE_X_MAX = 0.56
LOWER_PRIVATE_BADGE_Y_MIN = 0.10
LOWER_PRIVATE_BADGE_Y_MAX = 0.30

TEMPLATE_DIR = TEMPLATES_DIR

MATCH_THRESHOLD = 0.82

TEMPLATES = {
    "alliance_selected": ["alliance_chat_selected.png"],
    "alliance_unselected": ["alliance_chat_unselected.png"],

    "private_selected": ["private_chat_selected.png"],
    "private_unselected": ["private_chat_not_selected.png"],

    "private_menu": ["private_chat_select_menu.png"],
    "private_conversation": ["started_private_chat_with_someone.png"],

    "alliance_duel": ["alliance_Duel.png", "alliance_Duel_2.png"],

    "hq_button": ["HQ.png"],
    "world_button": ["world.png"],

    "unread_dot": ["reddot_unread.png", "unread.png"],
}



def load_template(name):

    images = []

    filenames = TEMPLATES[name]

    for filename in filenames:
        path = TEMPLATE_DIR / filename
        image = cv2.imread(str(path))

        if image is None:
            print(f"[WARNING] Template missing: {path}")
            continue
        images.append(image)
    return images


LOADED_TEMPLATES = {
    name: load_template(name)
    for name in TEMPLATES
}


def template_diagnostics() -> dict[str, dict[str, object]]:
    """Return loaded/missing template information for preflight checks."""
    diagnostics: dict[str, dict[str, object]] = {}

    for name, filenames in TEMPLATES.items():
        loaded = LOADED_TEMPLATES.get(name, [])
        diagnostics[name] = {
            "filenames": list(filenames),
            "loaded_count": len(loaded),
            "ok": bool(loaded),
        }

    return diagnostics



def match_template(screen, template):
    if template is None:
        return 0.0

    sh, sw = screen.shape[:2]
    th, tw = template.shape[:2]

    if th > sh or tw > sw:
        return 0.0

    result = cv2.matchTemplate(
        screen,
        template,
        cv2.TM_CCOEFF_NORMED
    )

    _, max_value, _, _ = cv2.minMaxLoc(result)

    return float(max_value)

def get_scores(screen):
    scores = {}
    for name, templates in LOADED_TEMPLATES.items():

        template_scores = []

        for template in templates:
            score = match_template(
                screen,
                template
            )

            template_scores.append(score)

        if template_scores:
            scores[name] = max(template_scores)
        else:
            scores[name] = 0.0

    return scores
def match_template_near_position(
    screen,
    template,
    center_x,
    center_y,
    radius=30
):
    if template is None:
        return 0.0

    screen_h, screen_w = screen.shape[:2]

    # Obszar, w którym kropka może się pojawić
    x1 = max(0, center_x - radius)
    y1 = max(0, center_y - radius)

    x2 = min(screen_w, center_x + radius)
    y2 = min(screen_h, center_y + radius)

    region = screen[y1:y2, x1:x2]

    if region.size == 0:
        return 0.0

    template_h, template_w = template.shape[:2]
    region_h, region_w = region.shape[:2]

    if (
        template_h > region_h
        or template_w > region_w
    ):
        return 0.0

    result = cv2.matchTemplate(
        region,
        template,
        cv2.TM_CCOEFF_NORMED
    )

    _, max_score, _, _ = cv2.minMaxLoc(result)

    return float(max_score)


def detect_unread_messages(screen):
    """Detect unread markers without treating unrelated red UI as chat.

    Template matching remains the primary signal.  Private Chat has a
    narrowly-scoped colour/shape fallback for the newer badge artwork, where
    the marker occupies the expected tab position but is not pixel-identical
    to the stored templates.
    """
    dot_templates = LOADED_TEMPLATES["unread_dot"]

    alliance_scores = []
    private_scores = []
    chat_scores = []

    for template in dot_templates:

        alliance_scores.append(
            match_template_near_position(
                screen,
                template,
                ALLIANCE_UNREAD_POS[0],
                ALLIANCE_UNREAD_POS[1],
                UNREAD_SEARCH_RADIUS
            )
        )

        private_scores.append(
            match_template_near_position(
                screen,
                template,
                PRIVATE_UNREAD_POS[0],
                PRIVATE_UNREAD_POS[1],
                UNREAD_SEARCH_RADIUS
            )
        )

        chat_scores.append(
            match_template_near_position(
                screen,
                template,
                CHAT_UNREAD_POS[0],
                CHAT_UNREAD_POS[1],
                UNREAD_SEARCH_RADIUS
            )
        )

    alliance_score = max(alliance_scores, default=0.0)
    private_score = max(private_scores, default=0.0)
    chat_scores = max(chat_scores, default=0.0)
    private_badge = has_private_unread_badge(screen)

    # A template score alone is never allowed to create a PM event. The upper
    # global badge is shared by PM and Group Chat, while old artwork can also
    # match unrelated dots. The bounded lower Private Chat sub-tab is the
    # proof used by Oczy.
    return {
        "alliance": alliance_score >= UNREAD_THRESHOLD,
        "private": private_badge,
        "chat": chat_scores >= UNREAD_THRESHOLD,
        "alliance_score": alliance_score,
        "private_score": private_score,
        "chat_score": chat_scores,
        "private_badge": private_badge,
    }


def has_private_unread_badge(screen):
    """Return whether a red badge is on the lower ``Private Chat`` sub-tab.

    The upper global Private Chat badge deliberately does not count: Last Z
    shows it for Group Chat activity too.  This test only admits the left
    half of the lower Private/Group chooser, so a dot on the lower Group Chat
    tab cannot switch PEva or start OCR.
    """
    if screen is None or not hasattr(screen, "shape") or len(screen.shape) != 3:
        return False

    height, width = screen.shape[:2]
    if height <= 0 or width <= 0:
        return False

    scale_x = width / REFERENCE_SCREEN_SIZE[0]
    scale_y = height / REFERENCE_SCREEN_SIZE[1]
    scale = min(scale_x, scale_y)
    if scale <= 0:
        return False

    x0 = max(0, int(round(width * LOWER_PRIVATE_BADGE_X_MIN)))
    x1 = min(width, int(round(width * LOWER_PRIVATE_BADGE_X_MAX)))
    y0 = max(0, int(round(height * LOWER_PRIVATE_BADGE_Y_MIN)))
    y1 = min(height, max(y0 + 1, int(round(height * LOWER_PRIVATE_BADGE_Y_MAX))))
    if x0 >= x1 or y0 >= y1:
        return False

    region = screen[y0:y1, x0:x1]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(hsv, (0, 120, 150), (12, 255, 255))
    high_red = cv2.inRange(hsv, (170, 120, 150), (179, 255, 255))
    mask = cv2.bitwise_or(low_red, high_red)
    component_count, _labels, stats, centers = cv2.connectedComponentsWithStats(mask)

    min_area = UNREAD_BADGE_MIN_AREA * scale * scale
    max_area = UNREAD_BADGE_MAX_AREA * scale * scale
    min_side = max(3.0, 11.0 * scale)
    max_side = max(8.0, 35.0 * scale)
    for component_index in range(1, component_count):
        x, y, component_width, component_height, area = stats[component_index]
        if not min_area <= area <= max_area:
            continue
        if not (min_side <= component_width <= max_side and min_side <= component_height <= max_side):
            continue
        aspect_ratio = component_width / component_height
        if not 0.55 <= aspect_ratio <= 1.6:
            continue
        center_x, center_y = centers[component_index]
        absolute_x, absolute_y = x0 + center_x, y0 + center_y
        if x0 <= absolute_x < x1 and y0 <= absolute_y < y1:
            return True
    return False

def classify_screen(scores):
    """Classify the UI with compact confidence/alternate evidence.

    ``CHAT_OPEN_UNCLASSIFIED`` is intentionally weaker than a tab/conversation
    classification.  It only says the chat shell is visibly present, allowing
    the navigator to re-plan or use an explicitly-safe tab action instead of
    treating transition frames as generic UNKNOWN.
    """
    candidates = {
        "HQ_BASE": float(scores.get("world_button", 0.0)),
        "WORLD_MAP": float(scores.get("hq_button", 0.0)),
        "ALLIANCE_CHAT": min(
            float(scores.get("alliance_selected", 0.0)),
            float(scores.get("private_unselected", 0.0)),
        ),
        "PRIVATE_CONVERSATION": min(
            float(scores.get("private_conversation", 0.0)),
            float(scores.get("private_selected", 0.0)),
        ),
        "PRIVATE_CHAT": min(
            float(scores.get("private_selected", 0.0)),
            float(scores.get("alliance_unselected", 0.0)),
        ),
    }
    ordered = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
    winner, confidence = ordered[0]
    alternate = ordered[1][0] if len(ordered) > 1 else "UNKNOWN"

    threshold = 0.88 if winner in {"HQ_BASE", "WORLD_MAP"} else MATCH_THRESHOLD
    if confidence >= threshold:
        return {
            "screen": winner,
            "confidence": confidence,
            "alternate": alternate,
            "chat_shell_evidence": winner in {
                "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION",
            },
        }

    chat_shell_score = max(
        float(scores.get("alliance_selected", 0.0)),
        float(scores.get("private_selected", 0.0)),
        float(scores.get("private_menu", 0.0)),
        float(scores.get("private_conversation", 0.0)),
    )
    if chat_shell_score >= MATCH_THRESHOLD:
        return {
            "screen": "CHAT_OPEN_UNCLASSIFIED",
            "confidence": chat_shell_score,
            "alternate": winner,
            "chat_shell_evidence": True,
        }
    return {
        "screen": "UNKNOWN",
        "confidence": confidence,
        "alternate": winner,
        "chat_shell_evidence": False,
    }


def detect_screen(scores):
    """Backward-compatible screen-only detector API."""
    return classify_screen(scores)["screen"]
