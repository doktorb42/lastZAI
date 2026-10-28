from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from trening.agency_store import AgencyStore
from trening.agency_types import AgencyDecision
from archive.legacy_runtime.chat_capture import ChatMessage
from charakter.humanization_store import HumanizationStore
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"


class _Capture:
    def __init__(self, message: ChatMessage) -> None:
        self.message = message

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        del timeout
        item, self.message = self.message, None
        return item


@dataclass
class _Pending:
    reply_id: str
    player_uid: str
    room_id: str
    text: str


class _Agent:
    def __init__(self) -> None:
        self.config = SimpleNamespace(own_uid=SELF_UID)
        self.ai = SimpleNamespace()
        self.created: list[_Pending] = []
        self.recorded = 0
        self.social_reply_text = ""
        self.social_reply_calls: list[dict[str, object]] = []

    def record_incoming_message(self, _message: ChatMessage) -> SimpleNamespace:
        self.recorded += 1
        return SimpleNamespace(status="recorded")

    def create_pending_reply(self, message: ChatMessage, text: str) -> SimpleNamespace:
        self.recorded += 1
        pending = _Pending(f"reply-{len(self.created)}", message.sender_uid, message.room_id, text)
        self.created.append(pending)
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def discard_reply(self, _reply_id: str) -> None:
        return None

    def generate_social_reply(self, _message: ChatMessage, **kwargs: object) -> SimpleNamespace:
        self.social_reply_calls.append(dict(kwargs))
        return SimpleNamespace(text=self.social_reply_text)


class _Agency:
    def __init__(self, decision: AgencyDecision) -> None:
        self.decision = decision

    def decide_reactive(self, _context: object) -> AgencyDecision:
        return self.decision

    def reactive_context(self, **kwargs: object) -> dict[str, object]:
        return dict(kwargs)

    def decide_proactive(self, _context: object, *, candidate_uids: set[str]) -> AgencyDecision:
        del candidate_uids
        return AgencyDecision.safe_fallback("AGENCY_PROACTIVE")

    def proactive_context(self, **kwargs: object) -> dict[str, object]:
        return dict(kwargs)


class AgencyRuntimeTests(unittest.TestCase):
    def _loop(self, decision: AgencyDecision, temp: Path, *, text: str = "hello there") -> tuple[PlayerLoop, _Agent, list[str]]:
        agent, logs = _Agent(), []
        human, agency = HumanizationStore(temp / "human.db"), AgencyStore(temp / "human.db")
        message = ChatMessage(room_id=ROOM_ID, group="custom", sender_uid=PEER_UID, sender_name="Player", text=text, seq_id=11)
        loop = PlayerLoop(capture=_Capture(message), agent=agent, executor=object(), agency_core=_Agency(decision), agency_store=agency, humanization_store=human, logger=logs.append, config=PlayerLoopConfig(agency_enabled=True, private_batch_window=0, auto_send=False))
        loop.set_runtime_availability(adb_connected=True, ai_available=True)
        return loop, agent, logs

    @staticmethod
    def _close(loop: PlayerLoop) -> None:
        loop.stop()
        if loop.humanization_store is not None:
            loop.humanization_store.close()
        if loop.agency_store is not None:
            loop.agency_store.close()

    def test_reply_uses_agency_text_without_legacy_generator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            loop, agent, logs = self._loop(AgencyDecision("AGENCY_REACTIVE", "REPLY", "CASUAL", "certainly, hi"), Path(temp))
            loop.run_once()
            self.assertEqual(agent.created[0].text, "certainly, hi")
            self.assertTrue(any(line.startswith("[AI REPLY]") for line in logs))
            self._close(loop)

    def test_ignore_and_wait_do_not_create_a_pending_reply(self) -> None:
        for choice in ("IGNORE", "WAIT"):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as temp:
                loop, agent, _logs = self._loop(AgencyDecision("AGENCY_REACTIVE", choice, "TEST"), Path(temp))
                loop.run_once()
                self.assertEqual(agent.created, [])
                self.assertEqual(agent.recorded, 1)
                self._close(loop)

    def test_social_bid_gets_preview_when_agency_wrongly_ignores_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            loop, agent, logs = self._loop(
                AgencyDecision("AGENCY_REACTIVE", "IGNORE", "TEST"),
                Path(temp),
                text="hey, what are you doing?",
            )
            agent.social_reply_text = "watching the map. what's up?"
            loop.run_once()
            self.assertEqual([item.text for item in agent.created], ["watching the map. what's up?"])
            self.assertEqual(len(agent.social_reply_calls), 1)
            self.assertTrue(any(line.startswith("[SOCIAL PREVIEW] overriding") for line in logs))
            self._close(loop)

    def test_social_preview_keeps_a_clear_close_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            loop, agent, _logs = self._loop(
                AgencyDecision("AGENCY_REACTIVE", "IGNORE", "TEST"),
                Path(temp),
                text="got it, thanks",
            )
            agent.social_reply_text = "unused"
            loop.run_once()
            self.assertEqual(agent.created, [])
            self.assertEqual(agent.social_reply_calls, [])
            self._close(loop)

    def test_suppressed_player_is_not_in_agency_candidate_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            loop, _agent, _logs = self._loop(AgencyDecision("AGENCY_REACTIVE", "WAIT", "TEST"), Path(temp))
            assert loop.humanization_store is not None
            loop.humanization_store.observe_incoming(player_uid=PEER_UID, room_id=ROOM_ID, text="a real message")
            loop.humanization_store.set_proactive_suppression(PEER_UID, indefinite=True)
            self.assertEqual(loop._safe_proactive_candidates(loop.now()), [])
            self._close(loop)


if __name__ == "__main__":
    unittest.main()
