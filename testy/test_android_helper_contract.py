from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android" / "peva-copy-bridge" / "app" / "src" / "main"
JAVA = ANDROID / "java" / "com" / "peva" / "copybridge"


class AndroidHelperContractTests(unittest.TestCase):
    def test_helper_has_no_internet_permission(self) -> None:
        manifest = (ANDROID / "AndroidManifest.xml").read_text(encoding="utf-8")

        self.assertIn('android:label="PEva Helper"', manifest)
        self.assertNotIn("android.permission.INTERNET", manifest)

    def test_copy_queue_is_a_one_time_local_handoff(self) -> None:
        provider = (JAVA / "BridgeProvider.java").read_text(encoding="utf-8")
        inbox = (JAVA / "ClipboardInbox.java").read_text(encoding="utf-8")

        self.assertIn("ClipboardInbox.take(getContext())", provider)
        self.assertIn("static synchronized JSONArray take(Context context)", inbox)
        self.assertIn('putQuietly(root, "messages", new JSONArray())', inbox)

    def test_panel_can_clear_only_helper_data(self) -> None:
        activity = (JAVA / "MainActivity.java").read_text(encoding="utf-8")

        self.assertIn("Usuń tymczasowe dane PEva", activity)
        self.assertIn("ClipboardInbox.clear(this)", activity)
        self.assertIn("OcrResultStore.clear(this)", activity)
        self.assertIn("schowek nie został zmieniony", activity)


if __name__ == "__main__":
    unittest.main()
