"""
Unit tests for MemoryService._get_extraction_model().

Resolution order (post-B5):
  1. self.extraction_model (LANGMEM_EXTRACTION_MODEL env / explicit arg)
  2. LITELLM_DEFAULT_MODEL env var
  3. RuntimeError — no asyncpg connection is opened

These tests verify correctness and confirm no DB connection is attempted.
They are NOT run by the bootstrapper CI suite (which lives under
bootstrapper/tests/); they require the backend's own dependencies
(asyncpg, httpx) but do not need the full Docker stack.
"""

import asyncio
import os
import unittest
from unittest.mock import patch, MagicMock, AsyncMock


class TestGetExtractionModel(unittest.TestCase):
    """Tests for MemoryService._get_extraction_model (env-var resolution, no DB)."""

    def _make_service(self, extraction_model: str = ""):
        """Construct a MemoryService with minimal env, bypassing store init."""
        # We import here to avoid module-level import errors if asyncpg / httpx
        # are not installed in the test runner environment.
        from memory_service import MemoryService  # type: ignore[import]

        svc = MemoryService.__new__(MemoryService)
        svc.extraction_model = extraction_model
        svc.database_url = "postgresql://user:pw@localhost/test"
        svc.litellm_url = "http://litellm:4000"
        svc.litellm_api_key = ""
        svc.weaviate_url = ""
        svc.namespace = "default"
        svc.max_facts = 1000
        svc.embedding_model = ""
        svc.store = None
        svc._initialized = False
        svc._init_lock = asyncio.Lock()
        svc.enabled = True
        return svc

    def _run(self, coro):
        return asyncio.run(coro)

    def test_explicit_extraction_model_returned(self):
        """When self.extraction_model is set, it is returned immediately."""
        svc = self._make_service(extraction_model="anthropic/claude-sonnet-4-5")
        result = self._run(svc._get_extraction_model())
        self.assertEqual(result, "anthropic/claude-sonnet-4-5")

    def test_env_var_returned_when_no_explicit_model(self):
        """LITELLM_DEFAULT_MODEL env var is returned when extraction_model is empty."""
        svc = self._make_service(extraction_model="")
        with patch.dict(os.environ, {"LITELLM_DEFAULT_MODEL": "ollama/qwen3.6:latest"}):
            result = self._run(svc._get_extraction_model())
        self.assertEqual(result, "ollama/qwen3.6:latest")

    def test_explicit_model_takes_priority_over_env(self):
        """self.extraction_model beats LITELLM_DEFAULT_MODEL when both are set."""
        svc = self._make_service(extraction_model="openai/gpt-4o")
        with patch.dict(os.environ, {"LITELLM_DEFAULT_MODEL": "ollama/qwen3.6:latest"}):
            result = self._run(svc._get_extraction_model())
        self.assertEqual(result, "openai/gpt-4o")

    def test_raises_runtime_error_when_both_unset(self):
        """RuntimeError is raised when neither extraction_model nor env var is set."""
        svc = self._make_service(extraction_model="")
        env = {k: v for k, v in os.environ.items() if k != "LITELLM_DEFAULT_MODEL"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                self._run(svc._get_extraction_model())
        self.assertIn("LITELLM_DEFAULT_MODEL", str(ctx.exception))

    def test_no_asyncpg_connect_called(self):
        """_get_extraction_model must NOT open a DB connection under any path."""
        import asyncpg  # type: ignore[import]

        svc = self._make_service(extraction_model="")
        with patch.dict(os.environ, {"LITELLM_DEFAULT_MODEL": "ollama/qwen3.6:latest"}):
            with patch.object(asyncpg, "connect", new_callable=AsyncMock) as mock_connect:
                self._run(svc._get_extraction_model())
                mock_connect.assert_not_called()

    def test_no_asyncpg_connect_called_on_error_path(self):
        """No DB connection even when both model sources are absent (error path)."""
        import asyncpg  # type: ignore[import]

        svc = self._make_service(extraction_model="")
        env = {k: v for k, v in os.environ.items() if k != "LITELLM_DEFAULT_MODEL"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(asyncpg, "connect", new_callable=AsyncMock) as mock_connect:
                with self.assertRaises(RuntimeError):
                    self._run(svc._get_extraction_model())
                mock_connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
