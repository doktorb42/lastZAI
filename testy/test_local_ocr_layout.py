from __future__ import annotations

import base64
import json
import unittest

from oczy.local_ocr import _parse_ocr_row


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class LocalOcrLayoutTests(unittest.TestCase):
    def test_optional_layout_is_decoded_from_the_same_one_shot_ocr_result(self) -> None:
        layout = json.dumps(
            {
                "version": 1,
                "width": 1080,
                "lines": [{"text": "hello", "left": 110, "top": 200, "right": 300, "bottom": 240}],
            }
        )
        raw = (
            f"status_b64={_encoded('done')}, text_b64={_encoded('hello')}, "
            f"header_b64={_encoded('LV.27 [ROXY]OG doktorb')}, layout_b64={_encoded(layout)}, captured_at=42"
        )

        result = _parse_ocr_row(raw)

        self.assertEqual(result.header_text, "LV.27 [ROXY]OG doktorb")
        self.assertIsNotNone(result.layout)
        assert result.layout is not None
        self.assertEqual(result.layout.width, 1080)
        self.assertEqual(result.layout.lines[0].text, "hello")

    def test_legacy_text_only_result_remains_supported(self) -> None:
        raw = f"status_b64={_encoded('done')}, text_b64={_encoded('hello')}, captured_at=42"

        result = _parse_ocr_row(raw)

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.header_text, "")
        self.assertIsNone(result.layout)
        self.assertFalse(result.composer_inspected)

    def test_composer_field_is_read_only_when_new_helper_reports_it(self) -> None:
        raw = (
            f"status_b64={_encoded('done')}, text_b64={_encoded('hello')}, "
            f"composer_b64={_encoded('my manual draft')}, captured_at=42"
        )

        result = _parse_ocr_row(raw)

        self.assertTrue(result.composer_inspected)
        self.assertEqual(result.composer_text, "my manual draft")


if __name__ == "__main__":
    unittest.main()
