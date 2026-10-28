"""Nagłówek PM może dać podgląd, ale nie może udawać UID."""

from __future__ import annotations

import unittest

from oczy.identity import is_last_z_sender_header, parse_game_sender_header, parse_private_header
from oczy.packets import ChatPacket, PacketBurstBuffer
from peva_runtime import vision_from_packet


class ConversationIdentityTests(unittest.TestCase):
    def test_game_style_header_extracts_display_name_for_preview(self) -> None:
        parsed = parse_private_header("LV.27 [ROXY]Cute Face")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "Cute Face")
        self.assertEqual(parsed.alliance, "ROXY")

    def test_vip_hq_level_and_gender_are_not_part_of_nickname(self) -> None:
        parsed = parse_private_header("V8 LV.27 ♂ DOKTORB")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "DOKTORB")
        self.assertIsNone(parsed.alliance)

    def test_ocr_letter_o_instead_of_gender_is_not_part_of_nickname(self) -> None:
        parsed = parse_private_header("V8 LV.27 O DOKTORB")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "DOKTORB")

    def test_corrupted_bubble_header_is_never_accepted_as_a_dedicated_title(self) -> None:
        self.assertTrue(is_last_z_sender_header("8 LV.27O DOKTORB"))
        self.assertIsNone(parse_private_header("8 LV.27O DOKTORB", dedicated_header=True))

    def test_corrupted_bubble_header_still_has_a_clean_future_group_sender_name(self) -> None:
        parsed = parse_game_sender_header("8 LV.27O DOKTORB")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "DOKTORB")

    def test_vip_and_level_without_gender_keep_the_real_nickname(self) -> None:
        parsed = parse_private_header("V8 LV.27 DOKTORB")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "DOKTORB")

    def test_future_group_sender_parser_strips_vip_level_and_gender(self) -> None:
        parsed = parse_game_sender_header("V7 LV.27 ♀ [ROXY]GENERAL GiO")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "GENERAL GiO")
        self.assertEqual(parsed.alliance, "ROXY")
        self.assertEqual(parsed.source, "game_sender_header_ocr")

    def test_future_group_sender_parser_also_accepts_no_gender_marker(self) -> None:
        parsed = parse_game_sender_header("V8 LV.27 DOKTORB")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "DOKTORB")

    def test_future_group_sender_parser_rejects_plain_body_text(self) -> None:
        self.assertIsNone(parse_game_sender_header("hello PEva"))

    def test_vip_hq_level_gender_and_alliance_keep_only_real_name(self) -> None:
        parsed = parse_private_header("V7 LV.27 ♂ [ROXY]GENERAL GiO")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "GENERAL GiO")
        self.assertEqual(parsed.alliance, "ROXY")

    def test_parenthesized_alliance_header_is_supported(self) -> None:
        parsed = parse_private_header("(ROXY)OG doktorb")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "OG doktorb")

    def test_bubble_or_menu_text_is_not_treated_as_identity(self) -> None:
        self.assertIsNone(parse_private_header("Reply OG PEva: hello"))
        self.assertIsNone(parse_private_header("hello PEva"))

    def test_plain_alias_requires_an_exact_human_confirmed_alias(self) -> None:
        self.assertIsNone(parse_private_header("DOKTORB"))
        parsed = parse_private_header("DOKTORB", trusted_aliases=("DOKTORB",))

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.source, "private_alias_ocr")

    def test_plain_centered_dedicated_header_stays_temporary(self) -> None:
        parsed = parse_private_header("DOKTORB", dedicated_header=True)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "DOKTORB")
        self.assertIsNone(parsed.alliance)
        self.assertEqual(parsed.source, "private_dedicated_header_ocr")

    def test_generic_profile_title_is_never_a_chat_recipient(self) -> None:
        self.assertIsNone(parse_private_header("Survivor Profile", dedicated_header=True))

    def test_plain_bubble_text_cannot_use_the_dedicated_header_exception(self) -> None:
        self.assertIsNone(parse_private_header("talk to me"))

    def test_confirmed_alias_wins_over_a_level_or_alliance_decoration(self) -> None:
        parsed = parse_private_header(
            "LV.27 [ROXY]DOKTORB",
            trusted_aliases=("DOKTORB",),
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_name, "DOKTORB")
        self.assertEqual(parsed.source, "private_alias_ocr")

    def test_packet_uses_only_dedicated_header_not_bubble_guessing(self) -> None:
        packet = ChatPacket("hello", 1, True, "ok", 1, conversation_header="LV.27 [ROXY]Cute Face")

        vision = vision_from_packet(packet)

        self.assertEqual(vision.messages[0].player_name, "Cute Face")
        self.assertEqual(vision.messages[0].source, "private_chat_local_ocr_header")

    def test_packet_accepts_plain_name_from_dedicated_header_field(self) -> None:
        packet = ChatPacket("hello", 1, True, "ok", 1, conversation_header="DOKTORB")

        vision = vision_from_packet(packet)

        self.assertEqual(vision.messages[0].player_name, "DOKTORB")

    def test_burst_discards_old_text_when_confirmed_conversation_changes(self) -> None:
        burst = PacketBurstBuffer(quiet_seconds=1.0, maximum_seconds=4.0)
        burst.add(
            ChatPacket("old message", 1, True, "ok", 1, conversation_header="LV.27 [ROXY]Cute Face"),
            now=10.0,
        )
        burst.add(
            ChatPacket("new message", 1, True, "ok", 2, conversation_header="LV.8 [ROXY]OG doktorb"),
            now=10.1,
        )

        packet = burst.take()

        self.assertIsNotNone(packet)
        assert packet is not None
        self.assertEqual(packet.text, "new message")
        self.assertEqual(packet.conversation_header, "LV.8 [ROXY]OG doktorb")

    def test_missing_header_cannot_inherit_preceding_identity(self) -> None:
        burst = PacketBurstBuffer(quiet_seconds=1.0, maximum_seconds=4.0)
        burst.add(
            ChatPacket("known chat", 1, True, "ok", 1, conversation_header="LV.27 [ROXY]Cute Face"),
            now=10.0,
        )
        burst.add(ChatPacket("unverified chat", 1, True, "ok", 2), now=10.1)

        packet = burst.take()

        self.assertIsNotNone(packet)
        assert packet is not None
        self.assertEqual(packet.text, "unverified chat")
        self.assertEqual(packet.conversation_header, "")

    def test_animated_fire_emote_is_nontext_without_layout_metadata(self) -> None:
        packet = ChatPacket(
            "Just knocked out fire number 5 for ya. Call me the flame tamer!",
            1,
            True,
            "visible_message_complete",
            1,
            conversation_header="LV.27 [ROXY]Cute Face",
        )

        vision = vision_from_packet(packet)

        self.assertTrue(vision.messages[0].nontext)
        self.assertIsNone(vision.messages[0].text)


if __name__ == "__main__":
    unittest.main()
