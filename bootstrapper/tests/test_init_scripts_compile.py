"""Every Python init/runtime script under any `services/<svc>/.../scripts/`
directory must parse cleanly via Python's in-memory compiler; every shell script under
the same tree must parse cleanly via `bash -n`.

Service scripts the bootstrapper package never imports live in several
`scripts/` layouts: `init/scripts/` (dedicated init containers),
`build/scripts/` (entrypoints + helpers baked into a service image — neo4j
backup/restore, jupyterhub `startup.sh`, local-deep-researcher's entrypoint
+ `init-config.py`), `pull/scripts/`, and `db/scripts/`. All run
at container build or start, so all need the syntax guard — the discovery
globs recurse the whole `services/` tree rather than enumerate subdirs
(which kept missing new layouts).

Note: both `catalog-init/scripts/` layout entries have been removed —
litellm's was removed in Part B6 (YAML + model_resolver flow in
`litellm-init` replaced it), and comfyui's `sync-catalog.py` was removed
in Part C5 (the manifest TSV written by comfyui_manifest_generator.py
replaced the DB-UPSERT flow).

The init container is the production loader — Python imports / bash
sources those files at container start. A purely-syntactic error
(duplicate kwarg, unclosed paren, mistyped indent, missing `fi`) lives
undetected until `docker compose up`, because nothing in the local
pytest suite imports them (they live outside the bootstrapper package).

PR #67's `fix(open-webui): timeouts + try/finally cleanup` introduced a
duplicate `timeout=30` kwarg in `register-tools.py:create_admin_user`;
the script crashed at module-import time on every open-webui-init
boot until 2026-06-08 when the audit caught it. This test is the
permanent guard.

Tests intentionally use Python's built-in `compile` (not `importlib`) so they
do NOT execute module-top-level code (init scripts read DATABASE_URL,
WEBUI_SECRET_KEY, etc. at import time and would crash in a clean
bootstrapper venv). Bash scripts use `bash -n` for the same reason
(parse-only, no execution).
"""
from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _discover_init_scripts() -> list[Path]:
    # Recursive: every *.py under ANY services/<svc>/.../scripts/ directory —
    # covers init/scripts, build/scripts, pull/scripts, db/scripts, and any
    # future <subdir>/scripts/ layout. catalog-init/scripts/ entries are gone:
    # litellm's was removed in Part B6, comfyui's sync-catalog.py in Part C5.
    # Enumerating specific subdirs missed entries, so glob the whole tree
    # instead of playing subdir whack-a-mole.
    return _tracked_service_scripts(".py")


def _discover_shell_init_scripts() -> list[Path]:
    return _tracked_service_scripts(".sh")


def _discover_node_init_scripts() -> list[Path]:
    return _tracked_service_scripts(".js")


def _tracked_service_scripts(suffix: str) -> list[Path]:
    tracked = subprocess.check_output(
        ["git", "ls-files", "services"], cwd=REPO_ROOT, text=True
    ).splitlines()
    return sorted(
        REPO_ROOT / relative
        for relative in tracked
        if "/scripts/" in relative and relative.endswith(suffix)
    )


def test_discovery_only_returns_tracked_service_scripts() -> None:
    tracked = {
        (REPO_ROOT / line).resolve()
        for line in subprocess.check_output(
            ["git", "ls-files", "services"], cwd=REPO_ROOT, text=True
        ).splitlines()
    }
    discovered = {
        path.resolve()
        for path in (
            _discover_init_scripts()
            + _discover_shell_init_scripts()
            + _discover_node_init_scripts()
        )
    }

    assert discovered <= tracked


@pytest.mark.parametrize(
    "script_path",
    _discover_init_scripts(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_init_script_compiles(script_path: Path) -> None:
    """The script must compile in memory — catches syntax-class bugs
    (duplicate kwargs, missing parens, mistyped indent) without
    executing any module-top-level code.
    """
    try:
        compile(script_path.read_bytes(), str(script_path), "exec")
    except SyntaxError as exc:
        pytest.fail(
            f"{script_path.relative_to(REPO_ROOT)} does not parse:\n{exc}"
        )


def test_at_least_one_script_discovered() -> None:
    """Belt-and-suspenders: if the glob ever returns nothing (the
    `services/*/init/scripts/` layout changed), this test fails loudly
    instead of trivially passing with zero parametrized cases.
    """
    scripts = _discover_init_scripts()
    assert scripts, (
        "No init scripts discovered under services/*/{init,build}/scripts/*.py "
        "— the layout may have changed; update this test's glob."
    )


@pytest.mark.parametrize(
    "script_path",
    _discover_shell_init_scripts(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_shell_init_script_parses(script_path: Path) -> None:
    """Every `.sh` init script must pass `bash -n` (parse-only).
    Catches unclosed `if`/`for`/`while`, mismatched quotes, missing
    `fi`/`done`. Skipped if `bash` is not on PATH (Windows CI).
    """
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    result = subprocess.run(
        [bash, "-n", str(script_path)],
        capture_output=True, text=True, check=False, timeout=10,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"{script_path.relative_to(REPO_ROOT)} does not parse:\n"
        f"{result.stderr}"
    )


@pytest.mark.parametrize(
    "script_path",
    _discover_node_init_scripts(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_node_init_script_parses(script_path: Path) -> None:
    """Every `.js` init script (e.g. the n8n consumer-workflow seeder, which
    must use node for HTTP because the n8n image's BusyBox wget has no
    `--method`) must pass `node --check` (parse-only, no execution).
    Skipped if `node` is not on PATH (it always is in the n8n image; the
    bootstrapper test venv may lack it).
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    result = subprocess.run(
        [node, "--check", str(script_path)],
        capture_output=True, text=True, check=False, timeout=10,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"{script_path.relative_to(REPO_ROOT)} does not parse:\n"
        f"{result.stderr}"
    )


@pytest.mark.parametrize(
    "script_path",
    _discover_init_scripts(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_init_script_no_duplicate_keyword_args(script_path: Path) -> None:
    """AST-walk every `Call` node, assert no keyword name appears twice in
    the same call.

    Belt-and-suspenders against the PR #67 duplicate `timeout=30` bug
    class. `py_compile` (in `test_init_script_compiles`) already catches
    duplicate kwargs with a `SyntaxError: keyword argument repeated: X`,
    but this AST test gives a clearer per-script failure message that
    names the duplicated kwarg, the line number, and the function being
    called — easier to triage than a bare SyntaxError from the parser.
    """
    tree = ast.parse(script_path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kwarg_names = [k.arg for k in node.keywords if k.arg is not None]
        seen: set[str] = set()
        for name in kwarg_names:
            if name in seen:
                violations.append(
                    f"  line {node.lineno}: duplicate kwarg '{name}' "
                    f"on Call to {ast.dump(node.func)[:60]}"
                )
            seen.add(name)
    assert not violations, (
        f"{script_path.relative_to(REPO_ROOT)} has duplicate kwargs:\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize(
    "script_path",
    _discover_init_scripts(),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_init_script_stdout_is_line_buffered(script_path: Path) -> None:
    """Init-container stdout is pipe-attached to ``docker logs``, so
    Python defaults to *block*-buffered output. A script that crashes or
    is killed mid-run with un-flushed lines in the buffer drops them
    silently — same blind-spot class as the PR #67 SyntaxError that hid
    behind ``register-tools.py`` for 24 hours.

    Every init script must opt into line-buffered stdout. Two equivalent
    patterns are accepted:

    - Module-level ``sys.stdout.reconfigure(line_buffering=True)`` —
      preferred for scripts with many ``print()`` sites
      (open-webui/init/register-*.py, lightrag/init/resolve-models.py).
    - Per-call ``flush=True`` on *every* ``print()`` — used by
      litellm/init/scripts/init.py.

    Either is fine; mixing isn't, and bare ``print()`` calls with
    neither guard are the bug this test catches.
    """
    tree = ast.parse(script_path.read_text(encoding="utf-8"))

    # Detection 1: module-level `sys.stdout.reconfigure(line_buffering=True)`.
    has_reconfigure = False
    for node in tree.body:
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        # Match `sys.stdout.reconfigure(...)` — Attribute chain ending in
        # reconfigure, called with line_buffering=True somewhere in kwargs.
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "reconfigure"):
            continue
        inner = func.value
        if not (isinstance(inner, ast.Attribute) and inner.attr == "stdout"):
            continue
        if not any(
            k.arg == "line_buffering"
            and isinstance(k.value, ast.Constant)
            and k.value.value is True
            for k in call.keywords
        ):
            continue
        has_reconfigure = True
        break
    if has_reconfigure:
        return

    # Detection 2: every `print(...)` call carries `flush=True`.
    bare_prints: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "print":
            pass
        elif isinstance(func, ast.Attribute) and func.attr == "print":
            pass
        else:
            continue
        if not any(
            k.arg == "flush"
            and isinstance(k.value, ast.Constant)
            and k.value.value is True
            for k in node.keywords
        ):
            bare_prints.append(f"  line {node.lineno}: print() without flush=True")
    assert not bare_prints, (
        f"{script_path.relative_to(REPO_ROOT)} has bare print() calls and no "
        f"`sys.stdout.reconfigure(line_buffering=True)` at module top:\n"
        + "\n".join(bare_prints)
        + "\n\nPick one: add the reconfigure line near the imports, OR add "
        "flush=True to every print() in this script."
    )
