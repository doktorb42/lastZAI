"""Loader promptów korzysta z jednego źródła konfiguracji."""

from __future__ import annotations

import unittest

import config.prompt_manager as legacy
from config import prompt_manager as active


class PromptManagerCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_types_and_default_prompt_directory(self) -> None:
        self.assertIs(legacy.PromptManager, active.PromptManager)
        self.assertIs(legacy.PromptError, active.PromptError)
        self.assertEqual(
            active.PromptManager().prompts_dir,
            active.Path(__file__).resolve().parents[1] / "config" / "prompts",
        )


if __name__ == "__main__":
    unittest.main()
