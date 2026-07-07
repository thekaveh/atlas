"""Regression guard: psql ``:'var'`` must NOT be used inside ``-c`` / ``-tAc`` strings.

psql variable interpolation — ``:'var'`` for a quoted SQL literal, ``:"var"``
for an identifier — is performed ONLY for SCRIPT input (``stdin`` / ``-f``),
never for the SQL passed inline to ``-c`` / ``-tAc`` / ``-Ac``. Inside a ``-c``
string the literal ``:'var'`` is sent to the server verbatim and raises
``ERROR: syntax error at or near ":"``; under ``set -eu`` that aborts the whole
init container, and because compose wires
``depends_on: <svc>-init: condition: service_completed_successfully`` the
service never starts.

This class of bug survived every prior pass because it is a psql *semantic*
defect, not a shell *syntax* one: ``bash -n`` and ``shellcheck`` both pass, and
the bootstrapper pytest suite never does a live Docker bring-up. It bit
``init-label-studio.sh`` / ``init-mlflow.sh`` (copy-pasted from each other) and
``init-langfuse.sh`` (idempotency-check variant), all fixed by piping each
statement through stdin — the convention ``init-airflow.sh`` /
``init-iceberg-rest.sh`` already use and document. This test prevents the
antipattern from recurring anywhere under ``services/*/**/scripts/*.sh``.

Detection: a single shell-source line that simultaneously contains a ``psql``
invocation, a ``-c``-family flag (a dash-flag whose letters include ``c``, e.g.
``-c`` / ``-tAc`` / ``-Atc`` / ``-ac``), and the ``:'`` server-side-variable
marker. That combination only appears in the broken inline form; the correct
stdin form puts the ``:'`` in a ``printf`` string on a line whose ``psql`` has
no ``-c`` (it reads ``-tA`` / ``-v ON_ERROR_STOP=1``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# A dash-flag whose letters include `c`: -c, -tAc, -Atc, -ac, -tcA, ...
# Boundary guards keep it from matching inside a longer word; the leading
# dash must be a standalone flag token (not the second dash of `--command`).
_CFLAG = re.compile(r"(?<![\w-])-[A-Za-z]*c[A-Za-z]*(?![\w-])")
# The psql server-side variable-literal marker (quoted form).
_PSQL_VAR = re.compile(r":'")


def _discover_shell_scripts() -> list[Path]:
    return sorted(REPO_ROOT.glob("services/*/**/scripts/*.sh"))


@pytest.mark.parametrize(
    "script_path",
    _discover_shell_scripts(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_psql_var_interpolation_inside_dash_c(script_path: Path) -> None:
    """No ``psql ... -c \"...:'var'...\"`` — :'var' only resolves via stdin / -f."""
    offenders: list[str] = []
    for lineno, line in enumerate(
        script_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if "psql" not in line:
            continue
        if not _CFLAG.search(line):
            continue
        if _PSQL_VAR.search(line):
            offenders.append(f"  line {lineno}: {line.strip()}")
    assert not offenders, (
        f"{script_path.relative_to(REPO_ROOT)} uses psql :'var' inside a -c "
        f"string. psql does NOT interpolate :'var' there (only in stdin/-f), "
        f"so the server raises a syntax error and (under set -eu) the init "
        f"container aborts — the service never starts. Pipe the statement "
        f"through stdin instead, e.g. "
        f"`printf \"SELECT ... :'var';\\n\" | psql ... -v var=...`. Found:\n"
        + "\n".join(offenders)
    )


def test_at_least_one_shell_script_scanned() -> None:
    """Belt-and-suspenders: fail loudly if the glob ever returns nothing."""
    assert _discover_shell_scripts(), (
        "No shell scripts discovered under services/*/**/scripts/*.sh — the "
        "layout may have changed; update this test's glob."
    )
