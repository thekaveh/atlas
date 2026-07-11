"""Generated secrets must never start with `-` or `_`, and consumers
of those secrets must use argparse-safe `--flag=VALUE` form.

The trap: `secrets.token_urlsafe()` emits `[A-Za-z0-9_-]`, so ~3% of
values start with `-` or `_`. Init scripts then call argparse-style
CLIs (e.g. `airflow users create --password ${PASS}`); argparse sees
the leading `-` on the secret value and rejects with
`argument -p: expected one argument` because it thinks the value is
another flag.

This was observed live on a user launch the morning after PR #35
merged: a generated `AIRFLOW_ADMIN_PASSWORD` whose first character was
a literal `-` made airflow-init exit rc=1 → the whole airflow family
failed to start. (The original docstring quoted the offending token
verbatim, which is why GitGuardian flagged this file as a generic-
secret leak — it had already been rotated locally, but the literal in
git history was the actual problem. Don't quote generated secrets in
test docstrings even after rotation.) Two layers of defense:

1. **bootstrapper-side guard**: `_cli_safe_token_urlsafe()` re-rolls
   the token if the first char is `-` or `_`. No future generated
   secret can hit this CLI-parsing class.
2. **consumer-side defense**: init-airflow.sh uses the equals form of
   every flag (single-token binding) which argparse always parses as
   one argument even if VALUE starts with `-`.

Both layers are independent — either alone would have prevented the
crash. Together they're belt-and-suspenders.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from utils.key_generator import _cli_safe_token_urlsafe

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INIT_SCRIPT = REPO_ROOT / "services" / "airflow" / "init" / "scripts" / "init-airflow.sh"


def test_cli_safe_token_urlsafe_never_starts_with_dash_or_underscore():
    """1000 samples — none should start with `-` or `_`."""
    for _ in range(1000):
        t = _cli_safe_token_urlsafe(18)
        assert t, "empty token"
        assert t[0] not in ("-", "_"), (
            f"_cli_safe_token_urlsafe leaked a leading dash/underscore: {t!r}"
        )


def test_cli_safe_token_urlsafe_preserves_url_safe_alphabet():
    """Body of the token may still legitimately contain `-` and `_`
    (URL-safe Base64). Only the FIRST char is constrained."""
    sample = _cli_safe_token_urlsafe(30)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", sample), (
        f"token escaped url-safe alphabet: {sample!r}"
    )


def test_all_token_urlsafe_callers_use_the_cli_safe_helper():
    """The trap is easy to re-introduce by calling
    `secrets.token_urlsafe()` directly. AST-scan key_generator.py and
    insist every `token_urlsafe` reference goes through the helper."""
    import ast
    src = (REPO_ROOT / "bootstrapper" / "utils" / "key_generator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    direct_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (isinstance(node.func.value, ast.Name)
                and node.func.value.id == "secrets"
                and node.func.attr == "token_urlsafe"):
                direct_calls.append(node.lineno)
    # The helper itself calls secrets.token_urlsafe — that's the one
    # legitimate site. Everything else must route through the helper.
    assert len(direct_calls) <= 1, (
        f"Found {len(direct_calls)} direct `secrets.token_urlsafe(...)` calls "
        f"in key_generator.py (lines {direct_calls}); only the "
        f"`_cli_safe_token_urlsafe` helper itself should call it directly. "
        f"All other call sites must go through the helper to avoid leading-"
        f"dash CLI parsing failures."
    )


def test_init_airflow_uses_equals_form_for_password_flags():
    """init-airflow.sh must use `--password=VALUE` (= form), not
    `--password VALUE` (space form), so argparse can't misinterpret a
    legitimately-leading-dash value as a flag. The bootstrapper-side
    guard makes this defensive, but the script's own discipline is
    the only line of defense against external secret rotation
    (user-supplied passwords).
    """
    body = INIT_SCRIPT.read_text(encoding="utf-8")

    # All `--password / --conn-password / --conn-login` occurrences with
    # a `${...}` value must use the = form. Match cases with a space
    # between the flag and a quoted ${...} value — those would break.
    SPACE_FORM = re.compile(
        r'--(?:conn-)?(?:password|login)\s+"?\$\{[A-Z_]+\}',
    )
    matches = SPACE_FORM.findall(body)
    assert not matches, (
        f"init-airflow.sh contains space-separated value flags that argparse "
        f"will mis-parse when the value starts with `-`: {matches}. "
        f"Use `--flag=\"${{VAR}}\"` form instead. See PR #37 history."
    )


def test_generate_lightrag_api_key_returns_prefixed_secret():
    from utils.key_generator import KeyGenerator
    gen = KeyGenerator()
    key = gen.generate_lightrag_api_key()
    assert key.startswith("sk-lightrag-")
    assert len(key) > len("sk-lightrag-") + 20  # token_urlsafe entropy floor


def test_generate_lightrag_rerank_adapter_token_returns_prefixed_secret():
    from utils.key_generator import KeyGenerator
    gen = KeyGenerator()
    key = gen.generate_lightrag_rerank_adapter_token()
    assert key.startswith("sk-lightrag-rerank-")
    assert len(key) > len("sk-lightrag-rerank-") + 20  # token_urlsafe entropy floor
