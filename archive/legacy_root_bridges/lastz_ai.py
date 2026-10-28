"""Mostek zgodności dla dawnej ścieżki klienta modelu.

Właściwy klient lokalnego modelu należy do ``mozg/lastz_ai.py``. Ten mały
plik zachowuje stare importy dla niezarchiwizowanych modułów oraz ręcznego
testu, bez utrzymywania drugiej kopii logiki.
"""

from mozg.lastz_ai import *  # noqa: F401,F403


if __name__ == "__main__":
    from mozg.lastz_ai import _interactive_test

    _interactive_test()
