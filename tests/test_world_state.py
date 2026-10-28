from __future__ import annotations

import json
import unittest

from archive.legacy_runtime.player_events import EventType, PlayerEvent
from archive.legacy_runtime.world_state import WorldState


class WorldStateTests(unittest.TestCase):
    def test_screen_projection_snapshot_and_safe_navigation(self) -> None:
        state = WorldState(adb_connected=True)

        changed = state.update_screen(
            "HQ_BASE",
            confidence=0.98,
            unread_private=True,
            unread_alliance=False,
            updated_at=123.0,
        )

        self.assertTrue(changed)
        self.assertTrue(state.in_hq)
        self.assertFalse(state.chat_open)
        self.assertTrue(state.is_safe_for_navigation())
        snapshot = state.snapshot()
        self.assertEqual(snapshot["screen"], "HQ_BASE")
        self.assertTrue(snapshot["unread_private"])
        json.dumps(snapshot)

    def test_unknown_is_fail_closed_and_chat_event_does_not_claim_open_conversation(self) -> None:
        state = WorldState(adb_connected=True)
        state.update_screen("UNKNOWN")
        self.assertFalse(state.is_safe_for_navigation())

        event = PlayerEvent(
            event_type=EventType.CHAT_MESSAGE,
            source="test",
            created_at=55.0,
            payload={
                "message": {
                    "room_id": "custom_1_2_v2",
                    "sender_uid": "1",
                    "text": "hello",
                }
            },
        )
        self.assertTrue(state.apply_event(event))
        self.assertIsNone(state.current_conversation_uid)
        self.assertIsNone(state.current_conversation_room_id)
        self.assertFalse(state.current_conversation_verified)
        self.assertEqual(state.last_chat_message_at, 55.0)

    def test_action_and_user_events_are_applied_without_ai(self) -> None:
        state = WorldState()
        state.apply_event(
            PlayerEvent(
                event_type=EventType.ACTION_COMPLETED,
                source="test",
                payload={"action": "open_chat", "status": "executed"},
            )
        )
        self.assertEqual(state.last_action, "open_chat")
        self.assertEqual(state.last_action_result, "executed")

        state.apply_event(
            PlayerEvent(event_type=EventType.USER_DECISION_REQUIRED, source="test")
        )
        self.assertTrue(state.waiting_for_user)
        state.apply_event(
            PlayerEvent(event_type=EventType.USER_DECISION_RECEIVED, source="test")
        )
        self.assertFalse(state.waiting_for_user)


if __name__ == "__main__":
    unittest.main()
