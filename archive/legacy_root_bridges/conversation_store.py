"""Mostek zgodności dla historii rozmów przeniesionej do Mózgu."""

from mozg.conversation_store import *  # noqa: F401,F403


if __name__ == "__main__":
    from mozg.conversation_store import _self_test

    _self_test()
