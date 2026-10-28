from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mozg.friendships import DEFER_DAYS, FriendshipStatus, FriendshipStore
from oczy.identity import ConversationHeader, parse_private_header


HEADER = ConversationHeader("OG doktorb", "ROXY", 0.97)


class FriendshipStoreTests(unittest.TestCase):
    def _store(self) -> tuple[tempfile.TemporaryDirectory[str], FriendshipStore]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return temporary, FriendshipStore(Path(temporary.name) / "friendships.json")

    def test_mutual_turn_threshold_creates_metadata_only_candidate(self) -> None:
        temporary, store = self._store()

        self.assertIsNone(store.observe_conversation(HEADER, user_turns=8, assistant_turns=1, now_ms=1_000))
        candidate = store.observe_conversation(HEADER, user_turns=6, assistant_turns=4, now_ms=2_000)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.status, FriendshipStatus.REVIEW)
        self.assertEqual(candidate.proposed_alias, "CAND_OG_DOKTORB")
        self.assertEqual(candidate.draft_message, "Hey, would you like to be friends?")
        saved = (Path(temporary.name) / "friendships.json").read_text(encoding="utf-8")
        self.assertNotIn("private message text", saved)
        self.assertEqual(json.loads(saved)["candidates"][0]["status"], "REVIEW")

    def test_defer_has_cooldown_and_block_never_reopens(self) -> None:
        _, store = self._store()
        candidate = store.observe_conversation(HEADER, user_turns=6, assistant_turns=4, now_ms=1_000)
        assert candidate is not None
        deferred = store.defer(candidate.candidate_id, now_ms=2_000)

        self.assertEqual(deferred.status, FriendshipStatus.DEFERRED)
        before = store.observe_conversation(
            HEADER,
            user_turns=6,
            assistant_turns=4,
            now_ms=deferred.next_eligible_at_ms - 1,
        )
        self.assertEqual(before.status, FriendshipStatus.DEFERRED)
        reopened = store.observe_conversation(
            HEADER,
            user_turns=6,
            assistant_turns=4,
            now_ms=deferred.next_eligible_at_ms + 1,
        )
        self.assertEqual(reopened.status, FriendshipStatus.REVIEW)
        self.assertEqual(reopened.proposal_count, 2)
        blocked = store.block(reopened.candidate_id, now_ms=deferred.next_eligible_at_ms + 2)
        self.assertEqual(blocked.status, FriendshipStatus.BLOCKED)
        self.assertEqual(
            store.observe_conversation(
                HEADER,
                user_turns=6,
                assistant_turns=4,
                now_ms=deferred.next_eligible_at_ms + DEFER_DAYS * 99,
            ).status,
            FriendshipStatus.BLOCKED,
        )

    def test_alias_is_confirmed_only_after_manual_friendship_steps(self) -> None:
        _, store = self._store()
        candidate = store.observe_conversation(HEADER, user_turns=6, assistant_turns=4, now_ms=1_000)
        assert candidate is not None

        with self.assertRaises(ValueError):
            store.confirm_alias(candidate.candidate_id, "DOKTORB", now_ms=2_000)
        sent = store.mark_offer_sent(candidate.candidate_id, now_ms=2_000)
        self.assertEqual(sent.status, FriendshipStatus.OFFER_SENT)
        needs_alias = store.mark_friend_added(candidate.candidate_id, now_ms=3_000)
        self.assertEqual(needs_alias.status, FriendshipStatus.NEEDS_ALIAS)
        friend = store.confirm_alias(candidate.candidate_id, "doktorb", now_ms=4_000)

        self.assertEqual(friend.status, FriendshipStatus.FRIEND)
        self.assertEqual(store.confirmed_aliases(), ("DOKTORB",))
        alias_header = parse_private_header("DOKTORB", trusted_aliases=store.confirmed_aliases())
        self.assertIsNotNone(alias_header)
        assert alias_header is not None
        self.assertEqual(alias_header.source, "private_alias_ocr")
        self.assertEqual(
            store.observe_conversation(alias_header, user_turns=6, assistant_turns=4, now_ms=5_000).candidate_id,
            candidate.candidate_id,
        )

    def test_auto_reply_needs_friend_alias_and_explicit_consent(self) -> None:
        _, store = self._store()
        candidate = store.observe_conversation(HEADER, user_turns=6, assistant_turns=4, now_ms=1_000)
        assert candidate is not None
        store.mark_offer_sent(candidate.candidate_id, now_ms=2_000)
        store.mark_friend_added(candidate.candidate_id, now_ms=3_000)
        friend = store.confirm_alias(candidate.candidate_id, "DOKTORB", now_ms=4_000)

        approved = store.set_auto_reply_approval(friend.candidate_id, True, now_ms=5_000)

        self.assertTrue(approved.auto_reply_approved)
        self.assertEqual(store.approved_auto_reply_aliases(), ("DOKTORB",))

    def test_existing_friend_can_be_registered_only_with_alias_and_profile_id(self) -> None:
        _, store = self._store()

        friend = store.register_confirmed_friend("DOCTOR TEST", now_ms=1_000)

        self.assertEqual(friend.status, FriendshipStatus.FRIEND)
        self.assertEqual(store.confirmed_aliases(), ("DOCTOR TEST",))
        self.assertFalse(friend.auto_reply_approved)
        with self.assertRaises(ValueError):
            store.register_confirmed_friend("DOCTOR TEST", now_ms=2_000)

    def test_user_can_remove_confirmed_friend_from_local_list_only(self) -> None:
        _, store = self._store()
        friend = store.register_confirmed_friend("DOCTOR TEST", now_ms=1_000)

        removed = store.remove_friend(friend.candidate_id)

        self.assertEqual(removed.final_alias, "DOCTOR TEST")
        self.assertEqual(store.snapshot(), ())

    def test_temporary_plain_header_never_creates_friend_proposal(self) -> None:
        _, store = self._store()
        header = ConversationHeader("DOKTORB", None, 0.86, "private_dedicated_header_ocr")

        candidate = store.observe_conversation(header, user_turns=8, assistant_turns=5, now_ms=1_000)

        self.assertIsNone(candidate)
        self.assertEqual(store.snapshot(), ())


if __name__ == "__main__":
    unittest.main()
