"""Zgodność ze starą ścieżką; właściwy kod jest w `oczy.local_ocr`."""

from oczy.local_ocr import *  # noqa: F401,F403
# A few historical tests/tools import this parser directly.  Wildcard imports
# intentionally omit underscore-prefixed names, so keep this narrow bridge
# until those callers have moved to ``oczy.local_ocr``.
from oczy.local_ocr import _parse_ocr_row  # noqa: F401
