from .screen_detection import (
    classify_screen,
    get_scores,
    detect_unread_messages
)


def get_game_state(screen_game):
    scores = get_scores(screen_game)

    classification = classify_screen(scores)
    screen = classification["screen"]

    unread = detect_unread_messages(screen_game)

    return {
        "screen": screen,

        "unread": {
            "private": unread["private"],
            "alliance": unread["alliance"],
            "chat": unread["chat"]
        },

        "debug": {
            "private_dot_score": unread["private_score"],
            "alliance_dot_score": unread["alliance_score"],
            "chat_dot_score": unread["chat_score"],
            "screen_confidence": classification["confidence"],
            "screen_alternate": classification["alternate"],
            "chat_shell_evidence": classification["chat_shell_evidence"],
        },
        "confidence": classification["confidence"],
        "alternate": classification["alternate"],
        "chat_shell_evidence": classification["chat_shell_evidence"],
    }
