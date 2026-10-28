from __future__ import annotations

from oczy.vision import UnreadRow
from oczy.private_chat import PrivateChatObservation


def test_copy_is_prepared_only_for_a_private_signal() -> None:
    quiet = PrivateChatObservation(1.0, "WORLD_MAP", 0.99, False, 0.1, (), ())
    tab_dot = PrivateChatObservation(1.0, "WORLD_MAP", 0.99, True, 0.9, (), (), True)
    row_dot = PrivateChatObservation(
        1.0, "PRIVATE_CHAT", 0.99, False, 0.1, (UnreadRow(800, 300, 0.95),), (),
    )

    assert not quiet.should_prepare_copy
    assert tab_dot.should_prepare_copy
    assert row_dot.should_prepare_copy
    assert not row_dot.is_private_conversation
