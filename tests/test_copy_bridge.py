from __future__ import annotations

import hashlib
import subprocess
import unittest

from oczy.android_bridge import CopyBridge, CopiedText, MessageBurst


def _runner_for(*outputs: bytes):
    responses = iter(outputs)

    def run(command, timeout):
        return subprocess.CompletedProcess(command, 0, next(responses), b"")

    return run


class CopyBridgeTests(unittest.TestCase):
    def test_fetch_reads_only_valid_exact_records(self) -> None:
        fingerprint = hashlib.sha256(b"hello").hexdigest()
        payload = (
            f"Row: 0 _id=one, text_b64=aGVsbG8, sha256={fingerprint}, captured_at=10\n"
            "Row: 1 _id=bad, text_b64=dGFtcGVyZWQ, sha256=" + "0" * 64 + ", captured_at=11\n"
            f"Row: 2 _id=one, text_b64=aGVsbG8, sha256={fingerprint}, captured_at=12\n"
        ).encode()
        bridge = CopyBridge(runner=_runner_for(payload))

        records = bridge.fetch()

        self.assertEqual(
            [(item.message_id, item.text, item.captured_at_ms) for item in records],
            [("one", "hello", 10)],
        )

    def test_arm_uses_private_pairing_token_and_bounded_arguments(self) -> None:
        commands = []
        status = (
            "Row: 0 status_b64=R290b3d5IHByemV6IDguMCBzLCBtYWtzLiAxIGtvcGlp, "
            "last_length=0, captured_at=0, captured_in_window=0, "
            "armed_until=9999999999999, pending=0"
        ).encode()
        responses = iter((b"token=" + b"a" * 32, b"Broadcast completed: result=0", status))

        def runner(command, timeout):
            commands.append(tuple(command))
            return subprocess.CompletedProcess(command, 0, next(responses), b"")

        bridge = CopyBridge(runner=runner)
        bridge.arm(window_ms=8000, max_copies=3)

        self.assertIn("com.peva.copybridge/.BridgeCommandReceiver", commands[1])
        self.assertIn("com.peva.copybridge.ARM", commands[1])
        self.assertIn("8000", commands[1])
        self.assertIn("3", commands[1])

    def test_arm_rejects_a_permanent_or_unbounded_listener_request(self) -> None:
        bridge = CopyBridge(runner=_runner_for())

        with self.assertRaises(ValueError):
            bridge.arm(window_ms=21000)
        with self.assertRaises(ValueError):
            bridge.arm(max_copies=13)

    def test_message_burst_waits_for_quiet_but_has_a_short_hard_deadline(self) -> None:
        first = CopiedText("one", "one", hashlib.sha256(b"one").hexdigest(), 1)
        second = CopiedText("two", "two", hashlib.sha256(b"two").hexdigest(), 2)
        burst = MessageBurst(idle_seconds=1.0, maximum_seconds=3.5)

        self.assertEqual(burst.add([first], now=10.0), 1)
        self.assertFalse(burst.ready(now=10.9))
        self.assertEqual(burst.add([second], now=10.9), 1)
        self.assertFalse(burst.ready(now=11.8))
        self.assertTrue(burst.ready(now=11.91))
        self.assertEqual([item.text for item in burst.take()], ["one", "two"])

    def test_fetch_ignores_malformed_provider_rows(self) -> None:
        bridge = CopyBridge(runner=_runner_for(b"Row: 0 text_b64=not-a-complete-row"))

        self.assertEqual(bridge.fetch(), ())


if __name__ == "__main__":
    unittest.main()
