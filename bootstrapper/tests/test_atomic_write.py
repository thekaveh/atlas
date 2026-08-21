from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from utils import atomic_write


def test_atomic_write_replace_failure_preserves_original_and_cleans_secret_tmp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / ".env"
    destination.write_text("SECRET=old\n", encoding="utf-8")
    os.chmod(destination, 0o600)

    monkeypatch.setattr(
        atomic_write.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        atomic_write.atomic_write_text(destination, "SECRET=new\n")

    assert destination.read_text(encoding="utf-8") == "SECRET=old\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [destination]


def test_atomic_write_preserves_mode_and_replaces_complete_content(
    tmp_path: Path,
) -> None:
    destination = tmp_path / ".env"
    destination.write_text("SECRET=old\n", encoding="utf-8")
    os.chmod(destination, 0o600)

    atomic_write.atomic_write_text(destination, "SECRET=new\n")

    assert destination.read_text(encoding="utf-8") == "SECRET=new\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600


def test_atomic_write_can_enforce_a_private_mode(tmp_path: Path) -> None:
    destination = tmp_path / "consumer.env"
    destination.write_text("TOKEN=old\n", encoding="utf-8")
    os.chmod(destination, 0o644)

    atomic_write.atomic_write_text(destination, "TOKEN=new\n", mode=0o600)

    assert destination.read_text(encoding="utf-8") == "TOKEN=new\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600


def test_atomic_write_falls_back_when_fchmod_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / ".env"
    monkeypatch.delattr(atomic_write.os, "fchmod")

    atomic_write.atomic_write_text(destination, "SECRET=new\n", mode=0o600)

    assert destination.read_text(encoding="utf-8") == "SECRET=new\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX-only")
def test_atomic_write_fsyncs_file_and_parent_directory(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[int] = []
    real_fsync = atomic_write.os.fsync

    def recording_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(atomic_write.os, "fsync", recording_fsync)

    atomic_write.atomic_write_text(tmp_path / ".env", "SECRET=new\n")

    assert len(calls) == 2


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX-only")
def test_atomic_write_tolerates_unsupported_directory_fsync(
    tmp_path: Path, monkeypatch
) -> None:
    import errno

    calls = 0
    real_fsync = atomic_write.os.fsync

    def unsupported_directory_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        real_fsync(fd)

    monkeypatch.setattr(atomic_write.os, "fsync", unsupported_directory_fsync)
    destination = tmp_path / ".env"

    atomic_write.atomic_write_text(destination, "SECRET=new\n")

    assert destination.read_text(encoding="utf-8") == "SECRET=new\n"
    assert calls == 2


def test_private_backups_are_exclusive_unique_and_mode_clamped(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".env"
    source.write_bytes(b"SECRET=value\r\nSECOND=line\r\n")
    os.chmod(source, 0o644)

    first = atomic_write.create_private_backup(source, version="v2")
    second = atomic_write.create_private_backup(source, version="v2")

    assert first != second
    assert first.name.startswith(".env.backup.v2.")
    assert second.name.startswith(".env.backup.v2.")
    assert first.read_bytes() == source.read_bytes()
    assert second.read_bytes() == source.read_bytes()
    assert os.stat(first).st_mode & 0o777 == 0o600
    assert os.stat(second).st_mode & 0o777 == 0o600


def test_private_backups_are_pruned_to_the_retention_cap(tmp_path: Path) -> None:
    """Every base-port change, key rotation and env migration snapshots `.env`.

    Without a cap those accumulate without bound, each holding the Supabase JWT
    signing keys, so a rotated secret stays readable on disk indefinitely.
    """
    source = tmp_path / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")

    made = [
        atomic_write.create_private_backup(source, keep=3)
        for _ in range(7)
    ]

    surviving = sorted(tmp_path.glob(".env.backup.*"))
    assert len(surviving) == 3, surviving
    # Every survivor is one of the snapshots actually created, and the newest
    # one is always retained. (Snapshots written inside the same second share an
    # mtime, so which of *those* survives is deliberately unspecified — only the
    # count and the newest are contractual.)
    assert set(surviving) <= set(made)
    assert made[-1] in surviving
    # Pruning never damages the source or the snapshot contents.
    assert source.read_text(encoding="utf-8") == "SECRET=value\n"
    for path in surviving:
        assert path.read_text(encoding="utf-8") == "SECRET=value\n"
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_private_backup_pruning_is_scoped_to_its_version_prefix(tmp_path: Path) -> None:
    """A v1 migration snapshot must not evict the unversioned rotation history."""
    source = tmp_path / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")

    plain = [atomic_write.create_private_backup(source, keep=2) for _ in range(2)]
    versioned = [
        atomic_write.create_private_backup(source, version="v1", keep=2)
        for _ in range(3)
    ]

    assert all(p.exists() for p in plain)
    assert len(list(tmp_path.glob(".env.backup.v1.*"))) == 2
    assert len(versioned) == 3


def test_private_backup_retention_can_be_disabled(tmp_path: Path) -> None:
    source = tmp_path / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")

    for _ in range(4):
        atomic_write.create_private_backup(source, keep=-1)

    assert len(list(tmp_path.glob(".env.backup.*"))) == 4


def test_unversioned_pruning_never_evicts_versioned_migration_snapshots(
    tmp_path: Path,
) -> None:
    """The v1/v2/v3 snapshots are env-migration rollback points.

    `.env.backup.*` also globs `.env.backup.v1.<ts>.<rand>`, so without an
    explicit version scope a plain rotation — a base-port change, a key
    regeneration — would count those as its own history and delete them.
    """
    source = tmp_path / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")

    migrations = [
        atomic_write.create_private_backup(source, version=v, keep=2)
        for v in ("v1", "v2", "v3")
    ]

    # A plain rotation with a tight cap must not touch any of them.
    for _ in range(4):
        atomic_write.create_private_backup(source, keep=1)

    for snapshot in migrations:
        assert snapshot.exists(), f"migration rollback point {snapshot.name} was pruned"
    assert len(list(tmp_path.glob(".env.backup.v*"))) == 3
    # ...and the plain history is still capped.
    plain = [
        p for p in tmp_path.glob(".env.backup.*")
        if not p.name.startswith(".env.backup.v")
    ]
    assert len(plain) == 1, plain


def test_retention_holds_for_a_relative_source_path(tmp_path: Path, monkeypatch) -> None:
    """`mkstemp` returns an abspath; `glob` returns the caller's path form.

    Comparing the two directly makes the just-written snapshot look like an old
    one, so the cap silently retains one fewer than asked — and at `keep=1` it
    deletes the very path being returned.
    """
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    relative = Path("sub/.env")
    relative.write_text("SECRET=value\n", encoding="utf-8")

    for _ in range(6):
        returned = atomic_write.create_private_backup(relative, keep=5)

    kept = list((tmp_path / "sub").glob(".env.backup.*"))
    assert len(kept) == 5, kept
    assert returned.exists(), "the returned snapshot must survive its own pruning"


def test_retention_of_one_keeps_exactly_the_new_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    relative = Path(".env")
    relative.write_text("SECRET=value\n", encoding="utf-8")

    for _ in range(4):
        returned = atomic_write.create_private_backup(relative, keep=1)

    kept = list(tmp_path.glob(".env.backup.*"))
    assert len(kept) == 1, kept
    assert returned.exists()
    assert kept[0].name == returned.name


def test_retention_survives_a_source_name_containing_glob_metacharacters(
    tmp_path: Path,
) -> None:
    """`ATLAS_ENV_FILE` is operator-supplied, so the basename is not ours.

    An unescaped `glob(f"{prefix}*")` turns `.env[dev].backup.*` into a
    character class that matches nothing, so pruning silently never runs — the
    one outcome this feature exists to prevent.
    """
    source = tmp_path / ".env[dev]"
    source.write_text("SECRET=value\n", encoding="utf-8")

    for _ in range(9):
        atomic_write.create_private_backup(source, keep=5)

    kept = list(tmp_path.glob("*.backup.*"))
    assert len(kept) == 5, kept


def test_env_assignment_guard_rejects_a_key_with_a_trailing_newline() -> None:
    """`re.match(r"...$")` accepts a trailing newline; `fullmatch` does not.

    That single case is the one metacharacter the guard exists to reject, and
    an anchored-with-`$` version let it through: `"KEY\\n"` was emitted as
    `KEY\\n=value`, which puts a bare `KEY` line in `.env` (read by
    docker-compose as "inherit from the host", i.e. unset) ahead of the real
    assignment on last-wins resolution.
    """
    from utils.atomic_write import assert_safe_env_assignment

    for bad_key in ("KEY\n", "\nKEY", "KEY\r", "A=B", "HAS SPACE", "9LEADING", ""):
        with pytest.raises(ValueError, match="not a valid environment variable name"):
            assert_safe_env_assignment(bad_key, "value")

    for bad_value in ("a\nB=c", "a\r\nB=c", "a\rB=c"):
        with pytest.raises(ValueError, match="multi-line"):
            assert_safe_env_assignment("GOOD_KEY", bad_value)

    # Valid assignments pass through, with the value coerced to str.
    assert assert_safe_env_assignment("GOOD_KEY", "fine") == "fine"
    assert assert_safe_env_assignment("_LEADING_UNDERSCORE", 63000) == "63000"


def test_env_lines_is_a_drop_in_for_splitlines_on_env_separators() -> None:
    """Same output SHAPE as `str.splitlines()`, narrower separator set.

    `re.split` differs from `splitlines()` by emitting a trailing `""` for text
    ending in a separator. Feeding that to an append-at-end rewriter adds a
    blank line — measured on all three migrations' `stamp_version`, which would
    have churned every user's `.env` by one line on upgrade.
    """
    from utils.atomic_write import env_lines

    for text in (
        "A=1\nB=2\n", "A=1\nB=2", "", "\n\n", "A=1\r\nB=2\r\n", "A=1\rB=2",
        "A=1", "\n", "\r\n",
    ):
        for keepends in (False, True):
            assert env_lines(text, keepends=keepends) == text.splitlines(keepends=keepends), text
        assert "".join(env_lines(text, keepends=True)) == text, text


def test_env_lines_does_not_split_on_the_separators_that_promote() -> None:
    """`splitlines()` splits on 8 separators the `.env` reader does not.

    Splitting on them and re-emitting with a real newline is what promoted an
    embedded fragment into a genuine assignment.
    """
    from utils.atomic_write import env_lines

    for ch in ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "):
        text = f"KEY=a{ch}SUPABASE_SERVICE_KEY=attacker\n"
        assert len(text.splitlines()) == 2, "precondition: splitlines would split here"
        assert env_lines(text) == [f"KEY=a{ch}SUPABASE_SERVICE_KEY=attacker"]


def _bootstrapper_packages(root: Path) -> list[str]:
    """Importable top-level packages under `bootstrapper/`.

    Derived, not hard-coded: the first version of the guard below listed only
    utils/core/services and so gave a PROVEN false pass on `generate_logo.py`,
    whose load-bearing `from ui.textual...` import it did not match.
    """
    found = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith((".", "_"))
    )
    return found or ["utils", "core", "services"]


def _is_type_checking_test(test: ast.AST) -> bool:
    """`TYPE_CHECKING` / `typing.TYPE_CHECKING` / an aliased `t.TYPE_CHECKING`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _executed_child_statements(stmt: ast.AST) -> list:
    """Child statements of a compound statement that DO run at import time.

    In EXECUTION order — body, then except handlers, then `else`, then
    `finally`. Appending handlers after `finalbody` reversed that, so a
    `finally:` bootstrap appeared to precede an import in an `except:` branch
    that really runs first.

    `if TYPE_CHECKING:` contributes only its `orelse`: the body never runs, but
    the `else` branch is exactly the one that does. Dropping the whole node
    lost that branch.

    `match` keeps its bodies under `cases`, not `body`.
    """
    if isinstance(stmt, ast.If) and _is_type_checking_test(stmt.test):
        return list(stmt.orelse)
    nested = list(getattr(stmt, "body", None) or [])
    for handler in getattr(stmt, "handlers", None) or []:
        nested.extend(handler.body)
    nested.extend(getattr(stmt, "orelse", None) or [])
    nested.extend(getattr(stmt, "finalbody", None) or [])
    for case in getattr(stmt, "cases", None) or []:
        nested.extend(case.body)
    return nested


def _module_level_statements(body: list) -> list:
    """Leaf statements that actually execute when the module is imported.

    Yields LEAVES only — a compound statement is recursed into, never emitted
    itself, or every nested import would be counted twice by the `ast.walk`
    scans below.

    Skips `FunctionDef`/`AsyncFunctionDef` (their bodies run on call). Does NOT
    skip `ClassDef`: a class body DOES execute at import, so an import inside
    one really can fail, and a bootstrap inside one really does take effect.
    """
    flat = []
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _is_compound(stmt):
            # Recurse only. Appending the container too would double-count its
            # contents, and an `if TYPE_CHECKING:` with no `else` would leak its
            # body back in as a "leaf".
            flat.extend(_module_level_statements(_executed_child_statements(stmt)))
        else:
            flat.append(stmt)
    return flat


def _is_compound(stmt: ast.AST) -> bool:
    """True when this statement holds other statements."""
    return any(
        getattr(stmt, attr, None)
        for attr in ("body", "handlers", "orelse", "finalbody", "cases")
    )


def _walk_executed(node: ast.AST):
    """Walk `node`, pruning `Lambda` bodies — they run on call, not here."""
    if isinstance(node, ast.Lambda):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_executed(child)


def _is_bootstrap(stmt: ast.AST, packages: list[str]) -> bool:
    """True when this statement performs a `sys.path` bootstrap itself."""
    for node in _walk_executed(stmt):
        if isinstance(node, ast.Call):
            target = ast.unparse(node.func)
            if target.endswith(("path.insert", "path.append", "addsitedir")):
                return True
        # `sys.path[0:0] = [...]`. Require an ASSIGNMENT of a SLICE: matching
        # any `<expr>.path[...]` also matched a plain READ, so an innocent
        # `ROOT = Path(sys.path[0])` short-circuited the scan and passed a
        # genuinely broken script.
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Slice)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "path"
        ):
            return True
    return False


def _first_party_names(stmt: ast.AST, wanted: set) -> list[str]:
    found = []
    for node in ast.walk(stmt):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in wanted:
                found.append(f"from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in wanted:
                    found.append(f"import {alias.name}")
    return found


def _first_party_imports_above_bootstrap(text: str, packages: list[str]) -> list[str]:
    """First-party imports that run before this file's `sys.path` bootstrap.

    Four attempts at this guard have false-passed. Three were regex-based; the
    fourth walked the AST but used `ast.walk`, which descends into function and
    class bodies — so a `def _boot(): sys.path.insert(...)` counted as a
    bootstrap it never performs, and a lazy import inside a helper counted as an
    offender it is not. Only statements that actually execute at import time are
    considered now, in source order.

    Fails CLOSED: a file with first-party imports and no module-level bootstrap
    has every one flagged rather than being skipped as vacuously fine.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise AssertionError(f"script does not parse: {exc}") from exc

    wanted = set(packages)
    offenders: list[str] = []
    for stmt in _module_level_statements(tree.body):
        if _is_bootstrap(stmt, packages):
            return offenders
        offenders.extend(_first_party_names(stmt, wanted))
    return offenders


def test_scripts_do_not_import_first_party_above_their_sys_path_bootstrap() -> None:
    """`py_compile` does not execute imports.

    Adding `from utils.atomic_write import env_lines` to
    `bootstrapper/scripts/reorg_user_env.py` above its own `sys.path.insert`
    bootstrap left the script raising `ModuleNotFoundError` on every
    invocation, and it compiled cleanly the whole time. A subprocess check
    cannot catch it either: `sys.executable` here is the uv venv, which has the
    bootstrapper installed as a package, so the import resolves regardless of
    ordering. Assert the ordering directly.

    Scoped to `bootstrapper/scripts/` deliberately, for two reasons.

    Python prepends the SCRIPT's own directory to `sys.path`, so `start.py` —
    at the package root — resolves `from services...` before its own
    (redundant) bootstrap. A script one level down gets `scripts/` on the path
    instead, so its bootstrap is load-bearing.

    And the repo-root `scripts/` tree must NOT be added: `docs` is a package
    name in both `bootstrapper/` and `scripts/`, and `check-docs-drift.py` and
    `number-markdown-headings.py` legitimately do
    `try: from scripts.docs... except ModuleNotFoundError: from docs...`.
    Their fallback branch names a package this guard treats as first-party, so
    widening the scope false-fails two scripts that work correctly — verified
    by running them.
    """
    root = Path(__file__).resolve().parents[1]
    packages = _bootstrapper_packages(root)
    scripts = sorted((root / "scripts").glob("*.py"))
    # `Path.glob` on a missing directory yields nothing without erroring, so
    # without this the test would go green while checking nothing.
    assert scripts, "the import-ordering guard found no scripts to check"
    for script in scripts:
        offenders = _first_party_imports_above_bootstrap(
            script.read_text(encoding="utf-8"), packages
        )
        assert not offenders, (
            f"{script.name}: {offenders} precede the sys.path bootstrap, so under "
            f"a bare interpreter the script raises ModuleNotFoundError — or, if "
            f"the import is `try:`-wrapped, silently takes its fallback branch, "
            f"which inside bootstrapper/scripts/ is equally a bug"
        )


#: (label, source, should_be_flagged). Every historical bypass of this guard is
#: pinned here — four earlier versions each passed a case in this table.
_IMPORT_GUARD_CASES = [
    ("import below bootstrap", "import sys\nsys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("import above bootstrap", "import sys\nfrom utils.a import b\nsys.path.insert(0,'x')\n", True),
    # bypass #1 — a comment mentioning the bootstrap fooled the substring scan
    ("decoy comment", "import sys\n# keep imports below the sys.path.insert bootstrap\n"
                      "from utils.a import b\nsys.path.insert(0,'x')\n", True),
    # bypass #2 — an indented bootstrap that never runs before module imports
    ("bootstrap inside def", "import sys\nfrom utils.a import b\n"
                             "def boot():\n    sys.path.insert(0,'x')\n", True),
    ("bootstrap in __main__", "import sys\nfrom utils.a import b\n"
                              "if __name__ == '__main__':\n    sys.path.insert(0,'x')\n", True),
    # bypass #3 — a try-wrapped import is not at column 0
    ("try-wrapped import above", "import sys\ntry:\n    from utils.a import b\n"
                                 "except ImportError:\n    b = None\nsys.path.insert(0,'x')\n", True),
    ("no bootstrap at all", "from utils.a import b\n", True),
    ("star import above", "import sys\nfrom utils import *\nsys.path.insert(0,'x')\n", True),
    ("plain import form", "import sys\nimport utils\nsys.path.insert(0,'x')\n", True),
    # must NOT flag:
    ("sys.path slice form", "import sys\nsys.path[0:0] = ['x']\nfrom utils.a import b\n", False),
    ("aliased sys", "import sys as s\ns.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("no first-party imports", "import os\nimport json\n", False),
    ("__future__ above", "from __future__ import annotations\nimport sys\n"
                         "sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    # bypass #4 — `ast.walk` descended into bodies, both directions
    ("lazy import in a helper above", "import sys\ndef load():\n    from utils.a import b\n"
                                      "sys.path.insert(0,'x')\nfrom utils.c import d\n", False),
    ("conditional bootstrap runs", "import sys\nif sys.platform != 'win32':\n"
                                   "    sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    # bypass #5 candidates — a `def`/`class`/`lambda` nested INSIDE an executed
    # block still cannot bootstrap, and `if TYPE_CHECKING:` never runs at all.
    ("def-with-bootstrap inside if", "import sys\nfrom utils.a import b\n"
                                     "if True:\n    def boot():\n        sys.path.insert(0,'x')\n", True),
    ("class-with-bootstrap inside if", "import sys\nfrom utils.a import b\n"
                                       "if True:\n    class C:\n        sys.path.insert(0,'x')\n", True),
    ("lambda bootstrap", "import sys\nfrom utils.a import b\n"
                         "boot = lambda: sys.path.insert(0,'x')\n", True),
    ("TYPE_CHECKING import above", "import sys\nfrom typing import TYPE_CHECKING\n"
                                   "if TYPE_CHECKING:\n    from utils.a import b\n"
                                   "sys.path.insert(0,'x')\n", False),
    ("try/finally bootstrap", "import sys\ntry:\n    pass\nfinally:\n"
                              "    sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("for-else bootstrap", "import sys\nfor _ in []:\n    pass\nelse:\n"
                           "    sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("nested if inside for", "import sys\nfor _ in [1]:\n    if True:\n"
                             "        sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("decorated def above bootstrap", "import sys\n@staticmethod\ndef h():\n"
                                      "    from utils.a import b\nsys.path.insert(0,'x')\n"
                                      "from utils.c import d\n", False),
    # `match` keeps its bodies under `cases`, not `body`.
    ("bootstrap inside match", "import sys\nmatch sys.platform:\n    case _:\n"
                               "        sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("import above a match bootstrap", "import sys\nfrom utils.a import b\n"
                                       "match sys.platform:\n    case _:\n"
                                       "        sys.path.insert(0,'x')\n", True),
    # every TYPE_CHECKING spelling, including an aliased module
    ("typing.TYPE_CHECKING", "import sys, typing\nif typing.TYPE_CHECKING:\n"
                             "    from utils.a import b\nsys.path.insert(0,'x')\n", False),
    ("aliased t.TYPE_CHECKING", "import sys, typing as t\nif t.TYPE_CHECKING:\n"
                                "    from utils.a import b\nsys.path.insert(0,'x')\n", False),
    # ...but an ordinary runtime conditional is NOT exempt
    ("if DEBUG is not type-checking", "import sys\nif DEBUG:\n    from utils.a import b\n"
                                      "sys.path.insert(0,'x')\n", True),
    # bypass #5 — a plain `sys.path[0]` READ is not a bootstrap
    ("sys.path[0] read is not a bootstrap",
     "import sys, pathlib\nROOT = pathlib.Path(sys.path[0]).resolve()\n"
     "from utils.a import b\nsys.path.insert(0, str(ROOT))\n", True),
    ("unrelated .path[0] read", "import sys\nx = cfg.path[0]\nfrom utils.a import b\n"
                                "sys.path.insert(0,'y')\n", True),
    ("sys.path slice ASSIGN is a bootstrap",
     "import sys\nsys.path[0:0] = ['x']\nfrom utils.a import b\n", False),
    # a class body DOES execute at import — unlike a function body
    ("import in a class body", "import sys\nclass C:\n    from utils.a import b\n"
                               "sys.path.insert(0,'x')\n", True),
    ("bootstrap in a class body", "import sys\nclass C:\n    sys.path.insert(0,'x')\n"
                                  "from utils.a import b\n", False),
    # a lambda body never runs at import
    ("lambda bootstrap before the import", "import sys\nboot = lambda: sys.path.insert(0,'x')\n"
                                           "from utils.a import b\n", True),
    # `if TYPE_CHECKING:` — the ELSE branch is the one that runs
    ("import in the TYPE_CHECKING else",
     "import sys\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\n"
     "else:\n    from utils.a import b\nsys.path.insert(0,'x')\n", True),
    ("bootstrap in the TYPE_CHECKING else",
     "import sys\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\n"
     "else:\n    sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    # execution order: except handlers run before `finally`
    ("except import before a finally bootstrap",
     "import sys\ntry:\n    import numpy\nexcept ImportError:\n"
     "    from utils.compat import numpy\nfinally:\n    sys.path.insert(0,'x')\n", True),
]


@pytest.mark.parametrize("label,source,flagged", _IMPORT_GUARD_CASES,
                         ids=[c[0] for c in _IMPORT_GUARD_CASES])
def test_import_ordering_guard_classifies_known_shapes(label, source, flagged) -> None:
    offenders = _first_party_imports_above_bootstrap(source, ["utils", "core", "services", "ui"])
    assert bool(offenders) is flagged, f"{label}: got {offenders}"


def test_import_ordering_guard_reports_an_unparseable_script() -> None:
    with pytest.raises(AssertionError, match="does not parse"):
        _first_party_imports_above_bootstrap("def broken(\n", ["utils"])
