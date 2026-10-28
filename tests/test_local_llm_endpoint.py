from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mozg.lastz_ai import AIConfig, LastZAI, LocalEndpointError, validate_local_base_url


class LocalLlmEndpointTests(unittest.TestCase):
    def test_loopback_endpoints_are_accepted(self) -> None:
        self.assertEqual(validate_local_base_url("http://127.0.0.1:8080"), "http://127.0.0.1:8080")
        self.assertEqual(validate_local_base_url("http://localhost:8080/"), "http://localhost:8080")

    def test_remote_endpoint_is_rejected_before_any_request(self) -> None:
        with self.assertRaisesRegex(LocalEndpointError, "Remote LLM endpoints"):
            LastZAI(config=AIConfig(base_url="http://model.example:8080"))

    def test_environment_override_cannot_redirect_the_model(self) -> None:
        with patch.dict(os.environ, {"LASTZ_LLM_URL": "http://model.example:8080"}, clear=False):
            with self.assertRaisesRegex(LocalEndpointError, "Remote LLM endpoints"):
                LastZAI()
