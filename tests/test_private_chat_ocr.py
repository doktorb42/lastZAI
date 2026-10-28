from __future__ import annotations

import json

from oczy.vision import IncomingBubble, UnreadRow
from oczy.private_chat import PrivateChatObservation
from oczy.private_reader import PrivateChatOcrCoordinator
from oczy.local_ocr import ScreenOcrResult


class FakeEyes:
    def __init__(self, *observations: PrivateChatObservation) -> None:
        self.observations = iter(observations)

    def observe_once(self) -> PrivateChatObservation:
        return next(self.observations)


class FakeOcr:
    def __init__(self, *results: ScreenOcrResult) -> None:
        self.results = iter(results)
        self.capture_count = 0

    def capture_visible_screen(self) -> None:
        self.capture_count += 1

    def wait_for_result(self, *, timeout_seconds: float = 8.0) -> ScreenOcrResult:
        assert timeout_seconds == 8.0
        return next(self.results)


class FakeRowOpener:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.rows: list[UnreadRow] = []

    def open_unread_row(self, row: UnreadRow) -> bool:
        self.rows.append(row)
        return self.succeeds


class FakeListRefresher:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls = 0

    def refresh_private_chat_list(self) -> bool:
        self.calls += 1
        return self.succeeds


def _conversation(*bubbles: IncomingBubble) -> PrivateChatObservation:
    return PrivateChatObservation(1.0, "PRIVATE_CONVERSATION", 0.99, False, 0.0, (), bubbles)


def test_reads_changed_visible_bubbles_once_and_persists_only_a_hash(tmp_path) -> None:
    first = _conversation(IncomingBubble(100, 300, 260, 360, 0.9))
    same = _conversation(IncomingBubble(100, 300, 260, 360, 0.9))
    changed = _conversation(IncomingBubble(100, 300, 260, 360, 0.9), IncomingBubble(100, 400, 280, 470, 0.9))
    result = ScreenOcrResult("done", "hello", 42)
    duplicate = ScreenOcrResult("done", "hello", 43)
    ocr = FakeOcr(result, duplicate)
    state = tmp_path / "eyes-state.json"
    coordinator = PrivateChatOcrCoordinator(eyes=FakeEyes(first, same, changed), ocr=ocr, state_path=state)

    assert coordinator.scan_once().kind == "new_text"
    assert coordinator.scan_once().kind == "already_seen_visual"
    assert coordinator.scan_once().kind == "duplicate_text"
    assert ocr.capture_count == 2
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["last_text_sha256"]
    assert saved["last_visual_sha256"]
    assert "hello" not in state.read_text(encoding="utf-8")


def test_profile_overlay_quarantines_background_chat_without_saving_a_hash(tmp_path) -> None:
    observation = _conversation(IncomingBubble(100, 300, 260, 360, 0.9))
    state = tmp_path / "eyes-state.json"
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(observation),
        ocr=FakeOcr(ScreenOcrResult("done", "background message", 42, "Survivor Profile")),
        state_path=state,
    )

    event = coordinator.scan_once()

    assert event.kind == "overlay_open"
    assert event.result is not None
    assert not state.exists()


def test_persisted_visual_hash_skips_ocr_after_a_restart(tmp_path) -> None:
    observation = _conversation(IncomingBubble(100, 300, 260, 360, 0.9))
    state = tmp_path / "eyes-state.json"
    first_ocr = FakeOcr(ScreenOcrResult("done", "first", 42))
    first = PrivateChatOcrCoordinator(eyes=FakeEyes(observation), ocr=first_ocr, state_path=state)
    assert first.scan_once().kind == "new_text"

    second_ocr = FakeOcr()
    second = PrivateChatOcrCoordinator(eyes=FakeEyes(observation), ocr=second_ocr, state_path=state)
    assert second.scan_once().kind == "already_seen_visual"
    assert second_ocr.capture_count == 0


def test_unread_list_signal_does_not_read_or_navigate() -> None:
    observation = PrivateChatObservation(
        1.0,
        "PRIVATE_CHAT",
        0.99,
        False,
        0.0,
        (UnreadRow(830, 390, 0.9),),
        (),
    )
    ocr = FakeOcr()
    coordinator = PrivateChatOcrCoordinator(eyes=FakeEyes(observation), ocr=ocr)

    event = coordinator.scan_once()

    assert event.kind == "unread_chat_needs_open"
    assert event.needs_conversation_open
    assert ocr.capture_count == 0


def test_quiet_visible_private_list_is_refreshed_once_then_cools_down() -> None:
    listed = PrivateChatObservation(1.0, "PRIVATE_CHAT", 0.99, False, 0.0, (), ())
    clock = [0.0]
    refresher = FakeListRefresher()
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(listed, listed, listed, listed),
        ocr=FakeOcr(),
        list_refresher=refresher,
        list_refresh_idle_seconds=30.0,
        list_refresh_cooldown_seconds=60.0,
        clock=lambda: clock[0],
    )

    assert coordinator.scan_once().kind == "idle"
    clock[0] = 29.0
    assert coordinator.scan_once().kind == "idle"
    clock[0] = 30.0
    assert coordinator.scan_once().kind == "private_chat_list_refresh_started"
    clock[0] = 59.0
    assert coordinator.scan_once().kind == "idle"
    assert refresher.calls == 1


def test_lower_private_badge_refreshes_through_top_level_tabs() -> None:
    listed = PrivateChatObservation(1.0, "PRIVATE_CHAT", 0.99, True, 0.9, (), (), True)
    refresher = FakeListRefresher()
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(listed),
        ocr=FakeOcr(),
        list_refresher=refresher,
        clock=lambda: 50.0,
    )

    event = coordinator.scan_once()

    assert event.kind == "private_chat_list_refresh_started"
    assert refresher.calls == 1


def test_group_activity_never_refreshes_the_private_tab() -> None:
    # Even if a weak visual classifier calls the Group Chat screen private,
    # the absence of the bounded top-level PM badge blocks every tab tap.
    group_like = PrivateChatObservation(1.0, "PRIVATE_CHAT", 0.82, True, 0.9, (), (), False)
    refresher = FakeListRefresher()
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(group_like),
        ocr=FakeOcr(),
        list_refresher=refresher,
        clock=lambda: 50.0,
    )

    assert coordinator.scan_once().kind == "idle"
    assert refresher.calls == 0


def test_confirmed_lower_private_badge_never_taps_the_lower_subtab() -> None:
    group_chat = PrivateChatObservation(1.0, "PRIVATE_CHAT", 0.99, True, 0.9, (), (), True)
    refresher = FakeListRefresher()
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(group_chat),
        ocr=FakeOcr(),
        list_refresher=refresher,
    )

    event = coordinator.scan_once()

    assert event.kind == "private_chat_list_refresh_started"
    assert refresher.calls == 1


def test_group_chat_dot_cannot_refresh_top_level_tabs() -> None:
    # This models the current lower Group Chat dot: no lower-left PM proof.
    group_chat = PrivateChatObservation(1.0, "PRIVATE_CHAT", 0.99, True, 0.9, (), (), False)
    refresher = FakeListRefresher()
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(group_chat),
        ocr=FakeOcr(),
        list_refresher=refresher,
    )

    assert coordinator.scan_once().kind == "idle"
    assert refresher.calls == 0


def test_refresh_never_leaves_an_open_private_conversation() -> None:
    conversation = _conversation()
    clock = [100.0]
    refresher = FakeListRefresher()
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(conversation),
        ocr=FakeOcr(),
        list_refresher=refresher,
        list_refresh_idle_seconds=15.0,
        clock=lambda: clock[0],
    )

    assert coordinator.scan_once().kind == "idle"
    assert refresher.calls == 0


def test_opens_exactly_one_marked_row_then_reads_confirmed_conversation(tmp_path) -> None:
    listed = PrivateChatObservation(
        1.0, "PRIVATE_CHAT", 0.99, True, 0.9, (UnreadRow(830, 390, 0.9),), (),
    )
    opened = _conversation(IncomingBubble(100, 300, 260, 360, 0.9))
    opener = FakeRowOpener()
    ocr = FakeOcr(ScreenOcrResult("done", "new message", 99))
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(listed, opened),
        ocr=ocr,
        row_opener=opener,
        state_path=tmp_path / "eyes-state.json",
    )

    event = coordinator.open_single_unread_and_read()

    assert event.kind == "new_text"
    assert opener.rows == [UnreadRow(830, 390, 0.9)]
    assert ocr.capture_count == 1


def test_refuses_to_choose_between_multiple_marked_rows() -> None:
    listed = PrivateChatObservation(
        1.0,
        "PRIVATE_CHAT",
        0.99,
        True,
        0.9,
        (UnreadRow(830, 390, 0.9), UnreadRow(830, 540, 0.9)),
        (),
    )
    opener = FakeRowOpener()
    coordinator = PrivateChatOcrCoordinator(eyes=FakeEyes(listed), ocr=FakeOcr(), row_opener=opener)

    event = coordinator.open_single_unread_and_read()

    assert event.kind == "ambiguous_unread_rows"
    assert opener.rows == []


def test_does_not_retry_the_same_row_after_a_failed_open() -> None:
    listed = PrivateChatObservation(
        1.0, "PRIVATE_CHAT", 0.99, True, 0.9, (UnreadRow(830, 390, 0.9),), (),
    )
    opener = FakeRowOpener(succeeds=False)
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(listed, listed),
        ocr=FakeOcr(),
        row_opener=opener,
    )

    assert coordinator.open_single_unread_and_read().kind == "row_open_failed"
    assert coordinator.open_single_unread_and_read().kind == "already_seen_unread_row"
    assert opener.rows == [UnreadRow(830, 390, 0.9)]


def test_requires_the_private_chat_list_before_opening_a_row() -> None:
    notification = PrivateChatObservation(
        1.0, "WORLD_MAP", 0.99, True, 0.9, (), (),
    )
    opener = FakeRowOpener()
    coordinator = PrivateChatOcrCoordinator(
        eyes=FakeEyes(notification), ocr=FakeOcr(), row_opener=opener,
    )

    event = coordinator.open_single_unread_and_read()

    assert event.kind == "unread_chat_list_not_visible"
    assert opener.rows == []
