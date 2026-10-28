from __future__ import annotations

import base64
import unittest

from oczy.android_bridge import _parse_status_row


def _row(status: str, *, armed_until: int) -> str:
    encoded = base64.urlsafe_b64encode(status.encode("utf-8")).decode("ascii").rstrip("=")
    return (
        "Row: 0 status_b64=" + encoded
        + ", last_length=0, captured_at=0, captured_in_window=0, armed_until="
        + str(armed_until) + ", pending=0"
    )


class CopyBridgeStatusTests(unittest.TestCase):
    def test_status_parser_decodes_metadata_only(self) -> None:
        parsed = _parse_status_row(
            _row("Gotowy przez 8.0 s; maks. 1 kopii", armed_until=9_999_999_999_999)
        )

        self.assertTrue(parsed.status.startswith("Gotowy przez"))
        self.assertEqual(parsed.armed_until_ms, 9_999_999_999_999)
        self.assertEqual(parsed.pending, 0)

    def test_stopped_status_is_not_armed(self) -> None:
        parsed = _parse_status_row(_row("Zatrzymany — schowek nie jest odczytywany", armed_until=0))

        self.assertFalse(parsed.armed)


if __name__ == "__main__":
    unittest.main()
