"""Poprawiacz deklaracji akcji Mózgu ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import mozg.action_claim_rewriter as legacy
import mozg.action_claim_rewriter as brain


class ActionClaimRewriterMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_rewriter(self) -> None:
        self.assertIs(legacy.ActionClaimRewriter, brain.ActionClaimRewriter)
        self.assertIs(legacy.RewriteAttempt, brain.RewriteAttempt)

    def test_fallback_never_claims_an_attack_was_done(self) -> None:
        reply = brain.ActionClaimRewriter().deterministic_fallback(
            parsed_command={"command_type": "ATTACK_PLAYER"},
            source_message_key="r1:1",
        )
        self.assertNotIn("attack", reply.casefold())


if __name__ == "__main__":
    unittest.main()
