"""Zgodność ze starą ścieżką; właściwy kod jest w `mozg.llama_server_manager`.

Zastępujemy wpis starego modułu tym samym obiektem modułu, aby narzędzia
legacy mogły nadal testowo podmieniać jego prywatne zależności. Nie powstaje
druga kopia implementacji ani stanu serwera.
"""

import sys

from mozg import llama_server_manager as _active_module

sys.modules[__name__] = _active_module
