"""Helpers shared by bootstrapper tests (imported as ``tests.shared_helpers``)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
LITELLM_INIT_PY = REPO_ROOT / "services/litellm/init/scripts/init.py"


def load_litellm_init_module():
    """Load services/litellm/init/scripts/init.py for its pure-Python helpers.

    init.py's module-level ``import psycopg2`` only resolves inside the
    litellm-init container image, so stub psycopg2 before exec_module.
    """
    sys.modules.setdefault("psycopg2", MagicMock())
    sys.modules.setdefault("psycopg2.extras", MagicMock())
    spec = importlib.util.spec_from_file_location("litellm_init", LITELLM_INIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["litellm_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def assert_contains(text, fragments):
    missing = tuple(fragment for fragment in fragments if fragment not in text)
    if missing:
        raise AssertionError(f"missing fragments: {missing!r}")
