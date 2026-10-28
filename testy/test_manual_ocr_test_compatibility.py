"""Ręczny OCR jest poza zwykłym startem i nie ładuje pakietów przy imporcie."""

from __future__ import annotations

import unittest
from pathlib import Path

import testy.manual_ocr_test as legacy
from testy import manual_ocr_test


class ManualOcrTestCompatibilityTests(unittest.TestCase):
    def test_legacy_name_exports_the_manual_test_without_loading_ocr(self) -> None:
        self.assertIs(legacy.main, manual_ocr_test.main)
        self.assertEqual(manual_ocr_test.ROOT, Path(__file__).resolve().parents[1])
        self.assertIsNone(manual_ocr_test.cv2)
        self.assertIsNone(manual_ocr_test.np)
        self.assertIsNone(manual_ocr_test.RapidOCR)


if __name__ == "__main__":
    unittest.main()
