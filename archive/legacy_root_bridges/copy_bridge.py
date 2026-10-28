"""Zgodność ze starą ścieżką; właściwy kod jest w `oczy.android_bridge`."""

from oczy.android_bridge import *  # noqa: F401,F403
# Compatibility for the existing metadata-only bridge test and any older
# diagnostic utility.  Production code lives in ``oczy.android_bridge``.
from oczy.android_bridge import _parse_status_row  # noqa: F401
