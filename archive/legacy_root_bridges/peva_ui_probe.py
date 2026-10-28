"""Mostek zgodności dla ręcznego testu interfejsu przeniesionego do testy/."""

from testy.manual_ui_probe import *  # noqa: F401,F403


if __name__ == "__main__":
    from testy.manual_ui_probe import main

    raise SystemExit(main())
