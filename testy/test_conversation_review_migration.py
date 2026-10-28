"""Ręczny przegląd danych treningowych ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import trening.conversation_review as legacy
import trening.conversation_review as training


class ConversationReviewMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_offline_review_functions(self) -> None:
        self.assertIs(legacy.render_player_review, training.render_player_review)
        self.assertIs(legacy.interactive_review, training.interactive_review)
        self.assertIs(legacy.render_agency_review, training.render_agency_review)
        self.assertIs(legacy.interactive_agency_review, training.interactive_agency_review)


if __name__ == "__main__":
    unittest.main()
