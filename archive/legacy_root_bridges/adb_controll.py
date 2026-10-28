"""Zgodna nazwa dla historycznego transportu ADB.

Właściwy moduł pozostaje poza zwykłą PEvą w ``archive/legacy_runtime``. Ten
plik nie kopiuje jego stanu: przekazuje import na dokładnie ten sam obiekt
modułu, aby stare testy i ręczne narzędzia zachowały przewidywalne podmiany.
Nowe Oczy i Ręce nie importują tej nazwy.
"""

from __future__ import annotations

import sys

from archive.legacy_runtime import adb_controll as _implementation


if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
