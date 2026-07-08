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

Detection: scan shell LOGICAL lines (continuation lines joined) for the
co-occurrence of a ``psql`` invocation, a ``-c``-family flag (a dash-flag whose
letters include ``c``, e.g. ``-c`` / ``-tAc`` / ``-Atc`` / ``-ac``), and the
``:'`` server-side-variable marker. Joining continuations is essential: the
natural way to write a long psql command — and the exact shape the original
bugs took — is ``psql`` on line 1 and ``-tAc \"...:'var'\"`` on a continuation
line, which a per-raw-line scan would miss. The correct stdin form puts ``:'``
in a ``printf`` string whose ``psql`` reads ``-tA`` / ``-v ON_ERROR_STOP=1``
(no ``-c``), so it is not flagged.
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


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Yield (start_line_no, joined_line) for shell LOGICAL lines — trailing
    backslash continuations collapsed into one string. psql commands are
    commonly split across continuation lines, and the per-raw-line scan this
    replaced missed that multi-line form (the original bug's shape).
    """
    out: list[tuple[int, str]] = []
    cur = ""
    start: int | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.rstrip()
        if start is None:
            start = lineno
        if stripped.endswith("\\"):
            cur += stripped[:-1] + " "
        else:
            cur += stripped
            out.append((start, cur))
            cur = ""
            start = None
    if cur:
        assert start is not None
        out.append((start, cur))
    return out


@pytest.mark.parametrize(
    "script_path",
    _discover_shell_scripts(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_psql_var_interpolation_inside_dash_c(script_path: Path) -> None:
    """No ``psql ... -c \"...:'var'...\"`` — :'var' only resolves via stdin / -f."""
    offenders: list[str] = []
    for start, logical in _logical_lines(script_path.read_text(encoding="utf-8")):
        if "psql" not in logical:
            continue
        if not _CFLAG.search(logical):
            continue
        if _PSQL_VAR.search(logical):
            offenders.append(f"  line {start}: {logical.strip()}")
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


# ── detector self-test: it must catch the multi-line known-bad form ─────────


def test_detector_catches_multiline_broken_form() -> None:
    """The original bugs were multi-line (psql line 1, -tAc \"...:'var'\" line 3).
    A line-based scan misses that; the logical-line join must catch it.
    """
    broken = (
        'role_exists=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \\\n'
        '    -v ON_ERROR_STOP=1 -v role="$ROLE" \\\n'
        '    -tAc "SELECT 1 FROM pg_roles WHERE rolname = :\'role\'")\n'
    )
    joined = _logical_lines(broken)
    assert any(
        "psql" in s and _CFLAG.search(s) and _PSQL_VAR.search(s) for _, s in joined
    ), "detector failed to catch the multi-line psql :'var'-in--c form"


def test_detector_passes_correct_stdin_form() -> None:
    correct = (
        'printf "SELECT 1 FROM pg_roles WHERE rolname = :\'role\';\\n" \\\n'
        '  | psql -h "$PGHOST" -v role="$ROLE" -v ON_ERROR_STOP=1 -tA\n'
    )
    joined = _logical_lines(correct)
    assert not any(
        "psql" in s and _CFLAG.search(s) and _PSQL_VAR.search(s) for _, s in joined
    ), "detector false-positive on the correct stdin form"
