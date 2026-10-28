"""Sprawdza nową publiczną ścieżkę modułu Oczu bez uruchamiania BlueStacks."""

from __future__ import annotations

import unittest


class EyesPackageTests(unittest.TestCase):
    def test_new_eyes_modules_import(self) -> None:
        from oczy.android_bridge import CopyBridge
        from oczy.local_ocr import ScreenOcrBridge
        from oczy.packets import PacketBurstBuffer
        from oczy.private_chat import PrivateChatEyes
        from oczy.private_reader import PrivateChatOcrCoordinator
        from oczy.watch import run_watch

        self.assertTrue(all((CopyBridge, ScreenOcrBridge, PacketBurstBuffer, PrivateChatEyes, PrivateChatOcrCoordinator, run_watch)))


if __name__ == "__main__":
    unittest.main()
