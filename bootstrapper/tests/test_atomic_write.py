from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import NamedTuple

import pytest

from utils import atomic_write


def test_atomic_write_replace_failure_preserves_original_and_cleans_secret_tmp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / ".env"
    destination.write_text("ROUNDTRIP=old\n", encoding="utf-8")
    os.chmod(destination, 0o600)

    monkeypatch.setattr(
        atomic_write.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        atomic_write.atomic_write_text(destination, "ROUNDTRIP=new\n")

    assert destination.read_text(encoding="utf-8") == "ROUNDTRIP=old\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [destination]


def test_atomic_write_preserves_mode_and_replaces_complete_content(
    tmp_path: Path,
) -> None:
    destination = tmp_path / ".env"
    destination.write_text("ROUNDTRIP=old\n", encoding="utf-8")
    os.chmod(destination, 0o600)

    atomic_write.atomic_write_text(destination, "ROUNDTRIP=new\n")

    assert destination.read_text(encoding="utf-8") == "ROUNDTRIP=new\n"
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

    atomic_write.atomic_write_text(destination, "ROUNDTRIP=new\n", mode=0o600)

    assert destination.read_text(encoding="utf-8") == "ROUNDTRIP=new\n"
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

    atomic_write.atomic_write_text(tmp_path / ".env", "ROUNDTRIP=new\n")

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

    atomic_write.atomic_write_text(destination, "ROUNDTRIP=new\n")

    assert destination.read_text(encoding="utf-8") == "ROUNDTRIP=new\n"
    assert calls == 2


def test_private_backups_are_exclusive_unique_and_mode_clamped(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".env"
    source.write_bytes(b"ROUNDTRIP=value\r\nSECOND=line\r\n")
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
    source.write_text("ROUNDTRIP=value\n", encoding="utf-8")

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
    assert source.read_text(encoding="utf-8") == "ROUNDTRIP=value\n"
    for path in surviving:
        assert path.read_text(encoding="utf-8") == "ROUNDTRIP=value\n"
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_private_backup_pruning_is_scoped_to_its_version_prefix(tmp_path: Path) -> None:
    """A v1 migration snapshot must not evict the unversioned rotation history."""
    source = tmp_path / ".env"
    source.write_text("ROUNDTRIP=value\n", encoding="utf-8")

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
    source.write_text("ROUNDTRIP=value\n", encoding="utf-8")

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
    source.write_text("ROUNDTRIP=value\n", encoding="utf-8")

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
    relative.write_text("ROUNDTRIP=value\n", encoding="utf-8")

    for _ in range(6):
        returned = atomic_write.create_private_backup(relative, keep=5)

    kept = list((tmp_path / "sub").glob(".env.backup.*"))
    assert len(kept) == 5, kept
    assert returned.exists(), "the returned snapshot must survive its own pruning"


def test_retention_of_one_keeps_exactly_the_new_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    relative = Path(".env")
    relative.write_text("ROUNDTRIP=value\n", encoding="utf-8")

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
    source.write_text("ROUNDTRIP=value\n", encoding="utf-8")

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
    packages = {
        d.name
        for d in root.iterdir()
        if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith((".", "_"))
    }
    # Deriving only PACKAGES repeated the original mistake one level up:
    # `start`, `stop`, `tracks` and `feature_flags` are top-level MODULES,
    # imported by name in 15+ files (`from tracks import load_tracks`), and
    # every one of them was invisible to the guard.
    modules = {f.stem for f in root.glob("*.py") if not f.stem.startswith("_")}
    return sorted(packages | modules) or ["utils", "core", "services"]


class _PathNames(NamedTuple):
    """What the names in THIS file are bound to.

    Every bypass this guard has shipped came from matching spellings instead of
    resolving names — `loader.search_path.append` counted as a bootstrap,
    `import sys as s` did not. Resolution happens once, here.
    """

    sys_mod: frozenset      # names bound to the `sys` MODULE
    sys_path: frozenset     # names bound to the `sys.path` LIST itself
    site_mod: frozenset     # names bound to the `site` MODULE
    addsitedir: frozenset   # names bound directly to `site.addsitedir`


def _imported_module_names(tree: ast.Module, module: str) -> set:
    """Local names bound to `module` by `import module [as x]`."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if alias.name == module:
                names.add(alias.asname or module)
    return names


def _imported_member_names(tree: ast.Module, module: str, member: str) -> set:
    """Local names bound by `from module import member [as x]`."""
    names = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module == module):
            continue
        for alias in node.names:
            if alias.name == member:
                names.add(alias.asname or member)
    return names


def _path_names(tree: ast.Module) -> _PathNames:
    return _PathNames(
        sys_mod=frozenset(_imported_module_names(tree, "sys")),
        # `from sys import path` binds the LIST — `path.insert(0, ...)` then
        # bootstraps with no `sys` anywhere in the statement.
        sys_path=frozenset(_imported_member_names(tree, "sys", "path")),
        site_mod=frozenset(_imported_module_names(tree, "site")),
        addsitedir=frozenset(_imported_member_names(tree, "site", "addsitedir")),
    )


def _sys_module_names(tree: ast.Module) -> set:
    """Local names bound to the `sys` module (`import sys [as s]`)."""
    return set(_imported_module_names(tree, "sys"))


# `typing_extensions` is the standard spelling for anything supporting older
# runtimes, and re-exports the identical flag.
_TYPING_MODULES = ("typing", "typing_extensions")


def _typing_module_names(tree: ast.Module) -> set:
    """Local names bound to the `typing` module (`import typing [as t]`)."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if alias.name in _TYPING_MODULES:
                names.add(alias.asname or alias.name)
    return names


def _type_checking_flag_names(tree: ast.Module) -> set:
    """Local names bound to `typing.TYPE_CHECKING` (`from typing import ...`)."""
    names = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module in _TYPING_MODULES):
            continue
        for alias in node.names:
            if alias.name == "TYPE_CHECKING":
                names.add(alias.asname or "TYPE_CHECKING")
    return names


def _typing_aliases(tree: ast.Module) -> tuple[set, set]:
    """Names for the `typing` module and for `TYPE_CHECKING`, from this file.

    Resolved from the file's own imports rather than pattern-matched, because
    matching any `<expr>.TYPE_CHECKING` also exempted `if cfg.TYPE_CHECKING:` —
    an unrelated object whose block DOES run at import.
    """
    return _typing_module_names(tree), _type_checking_flag_names(tree)


def _is_type_checking_test(test: ast.AST, modules: set, flags: set) -> bool:
    """Exactly `TYPE_CHECKING` from `typing` — bare, aliased, or dotted.

    Deliberately narrow, and fails toward FLAGGING: anything it cannot prove is
    typing's `TYPE_CHECKING` is treated as a block that runs. `if not
    TYPE_CHECKING:` and `if TYPE_CHECKING or DEBUG:` are not exempt either —
    both can execute.
    """
    if isinstance(test, ast.Name):
        return test.id in flags
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return isinstance(test.value, ast.Name) and test.value.id in modules
    return False


def _type_checking_branch(stmt: ast.AST, typing_names: tuple):
    """The one branch of an `if TYPE_CHECKING:` that runs, or None.

    `if TYPE_CHECKING:` contributes only its `orelse`; `if not TYPE_CHECKING:`
    contributes only its `body`. Anything else is not a TYPE_CHECKING guard and
    both branches are treated normally by the caller.
    """
    if not isinstance(stmt, ast.If):
        return None
    test = stmt.test
    if _is_type_checking_test(test, *typing_names):
        return list(stmt.orelse)          # only the else branch runs
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        if _is_type_checking_test(test.operand, *typing_names):
            return list(stmt.body)        # `if not TYPE_CHECKING:` — body runs
    return None


def _executed_child_statements(stmt: ast.AST, typing_names: tuple) -> list:
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
    branch = _type_checking_branch(stmt, typing_names)
    if branch is not None:
        return branch
    nested = list(getattr(stmt, "body", None) or [])
    for handler in getattr(stmt, "handlers", None) or []:
        nested.extend(handler.body)
    nested.extend(getattr(stmt, "orelse", None) or [])
    nested.extend(getattr(stmt, "finalbody", None) or [])
    for case in getattr(stmt, "cases", None) or []:
        nested.extend(case.body)
    return nested


def _module_level_statements(body: list, typing_names: tuple) -> list:
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
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # The BODY runs on call (or, for a class, below) — but the HEADER
            # is evaluated right here. Dropping the whole node dropped the
            # decorators, default arguments and base-class expressions with it,
            # so a bootstrap written in a default argument read as absent.
            flat.extend(_header_expressions(stmt))
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _is_compound(stmt):
            # Recurse only. Appending the container too would double-count its
            # contents, and an `if TYPE_CHECKING:` with no `else` would leak its
            # body back in as a "leaf".
            flat.extend(_module_level_statements(
                _executed_child_statements(stmt, typing_names), typing_names
            ))
        else:
            flat.append(stmt)
    return flat


def _header_expressions(stmt: ast.AST) -> list:
    """Def/class header expressions, which DO evaluate where they are written.

    Annotations are deliberately excluded: under `from __future__ import
    annotations` they are never evaluated at all.
    """
    exprs = _attr_list(stmt, "decorator_list")
    exprs.extend(_default_expressions(getattr(stmt, "args", None)))
    exprs.extend(_attr_list(stmt, "bases"))
    exprs.extend(kw.value for kw in _attr_list(stmt, "keywords"))
    return [ast.Expr(value=expr) for expr in exprs]


def _attr_list(node: ast.AST, attr: str) -> list:
    """`node.attr` as a list, tolerating both absent and None."""
    return list(getattr(node, attr, None) or [])


def _default_expressions(args) -> list:
    """Default-argument expressions, which evaluate at `def` time.

    `kw_defaults` is positional and holds None for keyword-only arguments that
    have no default — those slots are gaps, not expressions.
    """
    if args is None:
        return []
    slots = _attr_list(args, "defaults") + _attr_list(args, "kw_defaults")
    return [slot for slot in slots if slot is not None]


def _is_compound(stmt: ast.AST) -> bool:
    """True when this statement holds other statements."""
    return any(
        getattr(stmt, attr, None)
        for attr in ("body", "handlers", "orelse", "finalbody", "cases")
    )


def _walk_executed(node: ast.AST):
    """Walk `node`, pruning deferred scopes.

    `Lambda` and `GeneratorExp` bodies do not run where they are written — a
    genexp is only advanced when consumed. List/set/dict comprehensions DO
    evaluate at that point, so they must stay.
    """
    if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
        return
    yield node
    for child in _reachable_children(node):
        yield from _walk_executed(child)


def _reachable_children(node: ast.AST):
    """Child nodes minus the ones a LITERAL constant makes unreachable.

    `_ = False and sys.path.insert(0,'x')` contains a path mutation that never
    runs, yet it counted as this file's bootstrap and short-circuited the
    scan — so every offender below it went unreported. A FALSE PASS.

    Deliberately narrow: only a literal constant guard is treated as decidable.
    A real condition (`if cfg.dev: sys.path.insert(...)`) still counts as a
    bootstrap, matching how compound statements are handled elsewhere here.
    """
    if isinstance(node, ast.IfExp) and isinstance(node.test, ast.Constant):
        taken = node.body if node.test.value else node.orelse
        return [node.test, taken]
    if isinstance(node, ast.BoolOp):
        return _reachable_operands(node)
    return list(ast.iter_child_nodes(node))


def _reachable_operands(node: ast.BoolOp) -> list:
    """`and`/`or` operands up to the first literal that decides the result."""
    short_circuits_on = isinstance(node.op, ast.Or)
    reachable = []
    for operand in node.values:
        reachable.append(operand)
        if isinstance(operand, ast.Constant) and bool(operand.value) is short_circuits_on:
            break  # nothing after this can be evaluated
    return reachable


def _is_sys_path(node: ast.AST, names: _PathNames) -> bool:
    """True for anything that IS the `sys.path` list.

    Three spellings reach the same list: `<sys-alias>.path`, a bare name bound
    by `from sys import path`, and the `os.sys.path` re-export chain. Matching
    only the first missed two working bootstraps; matching any attribute merely
    NAMED `path` counted `loader.search_path` as one.
    """
    if isinstance(node, ast.Name):
        return node.id in names.sys_path
    if not (isinstance(node, ast.Attribute) and node.attr == "path"):
        return False
    base = node.value
    if isinstance(base, ast.Name):
        return base.id in names.sys_mod
    return isinstance(base, ast.Attribute) and base.attr == "sys"


def _is_bootstrap(stmt: ast.AST, names: _PathNames, helpers: frozenset) -> bool:
    """True when this statement performs a `sys.path` bootstrap itself.

    The callee is resolved against the file's own `sys` imports. A bare suffix
    test on the unparsed callee matched any receiver ending in `.path`, so
    `loader.search_path.append('x')` read as a bootstrap and short-circuited
    the scan — the same defect class as the `sys.path[0]` read.
    """
    return any(
        _is_path_mutating_call(node, names)
        or _is_path_rebinding(node, names)
        or _is_local_bootstrap_call(node, helpers)
        for node in _walk_executed(stmt)
    )


def _is_local_bootstrap_call(node: ast.AST, helpers: frozenset) -> bool:
    """A call to a module-level function whose body bootstraps `sys.path`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in helpers
    )


def _bootstrapping_helpers(tree: ast.Module, names: _PathNames) -> frozenset:
    """Module-level functions whose body performs a `sys.path` bootstrap.

        def _boot(): sys.path.insert(0, str(ROOT))
        _boot()
        from utils.x import y

    is a real and common idiom. Dropping every `FunctionDef` left the call site
    looking inert, so the file below it was reported broken when it is not.
    """
    found = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            _is_path_mutating_call(child, names) or _is_path_rebinding(child, names)
            for child in ast.walk(node)
        ):
            found.add(node.name)
    return frozenset(found)


_PATH_MUTATORS = ("insert", "append", "extend")


def _is_path_mutating_call(node: ast.AST, names: _PathNames) -> bool:
    """`sys.path.insert/append(...)` or `site.addsitedir(...)`.

    The callee is resolved against the file's own `sys` imports. A bare suffix
    test on the unparsed callee matched any receiver ending in `.path`, so
    `loader.search_path.append('x')` read as a bootstrap and short-circuited
    the scan — the same defect class as the `sys.path[0]` read.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in names.addsitedir          # `from site import addsitedir`
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "addsitedir":
        # The receiver is resolved here too. A bare `attr == "addsitedir"`
        # test fired for ANY object, so `loader.addsitedir('x')` read as a
        # bootstrap and short-circuited the scan — a FALSE PASS, and the same
        # defect this function was written to fix for `.append`.
        return isinstance(func.value, ast.Name) and func.value.id in names.site_mod
    return func.attr in _PATH_MUTATORS and _is_sys_path(func.value, names)


def _is_path_rebinding(node: ast.AST, names: _PathNames) -> bool:
    """`sys.path = ...`, `sys.path += ...`, `sys.path[...] = ...`.

    Requires a STORE context throughout: matching any `<expr>.path[...]` also
    matched a plain READ, so an innocent `ROOT = Path(sys.path[0])`
    short-circuited the scan and passed a genuinely broken script. Store alone
    is a sharper filter than the old `isinstance(node.slice, ast.Slice)` test,
    which missed the equally-real `sys.path[0] = x`.
    """
    if isinstance(node, ast.AugAssign):
        return _is_sys_path(node.target, names)
    if isinstance(node, ast.Assign):
        return any(_is_sys_path(target, names) for target in node.targets)
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Store)
        and _is_sys_path(node.value, names)
    )


def _dynamic_import_name(node: ast.AST) -> str | None:
    """The literal module name in `importlib.import_module(...)`/`__import__(...)`.

    Only a constant string argument counts — a computed name is genuinely
    undecidable here, and guessing would produce false failures.
    """
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    is_dynamic = (
        (isinstance(func, ast.Name) and func.id == "__import__")
        or (isinstance(func, ast.Attribute) and func.attr == "import_module")
    )
    if not is_dynamic:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _first_party_names(stmt: ast.AST, wanted: set) -> list[str]:
    found = []
    for node in ast.walk(stmt):
        dynamic = _dynamic_import_name(node)
        if dynamic and dynamic.split(".")[0] in wanted:
            # `importlib.import_module("utils.a")` above a bootstrap fails
            # exactly like the `from utils.a import ...` it stands in for.
            found.append(f"import {dynamic}")
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
    names = _path_names(tree)
    helpers = _bootstrapping_helpers(tree, names)
    for stmt in _module_level_statements(tree.body, _typing_aliases(tree)):
        if _is_bootstrap(stmt, names, helpers):
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
    # NOT a `__main__` test — the guard has no notion of `__main__`. What it
    # pins is that an import ABOVE a conditional bootstrap is flagged. The old
    # label implied an understanding that does not exist, and the case passed
    # for a different reason than it claimed.
    ("import above a conditional bootstrap", "import sys\nfrom utils.a import b\n"
                              "if __name__ == '__main__':\n    sys.path.insert(0,'x')\n", True),
    # The reversed shape, stated explicitly: a branch is treated as executed,
    # so the bootstrap counts and the import below it is fine. That is the
    # deliberate design (see `_executed_child_statements`), not an oversight.
    ("conditional bootstrap above an import", "import sys\n"
                              "if __name__ == '__main__':\n    sys.path.insert(0,'x')\n"
                              "from utils.a import b\n", False),
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
    # TYPE_CHECKING is resolved from the file's own imports, not pattern-matched
    ("cfg.TYPE_CHECKING is not typing's", "import sys\nif cfg.TYPE_CHECKING:\n"
                                          "    from utils.a import b\nsys.path.insert(0,'x')\n", True),
    ("TYPE_CHECKING name without the import", "import sys\nif TYPE_CHECKING:\n"
                                              "    from utils.a import b\nsys.path.insert(0,'x')\n", True),
    ("aliased flag TYPE_CHECKING as TC", "import sys\nfrom typing import TYPE_CHECKING as TC\n"
                                         "if TC:\n    from utils.a import b\nsys.path.insert(0,'x')\n", False),
    ("aliased module typing as t", "import sys\nimport typing as t\nif t.TYPE_CHECKING:\n"
                                   "    from utils.a import b\nsys.path.insert(0,'x')\n", False),
    ("not TYPE_CHECKING still runs", "import sys\nfrom typing import TYPE_CHECKING\n"
                                     "if not TYPE_CHECKING:\n    from utils.a import b\n"
                                     "sys.path.insert(0,'x')\n", True),
    # comprehensions and genexps have their own scope but DO evaluate here
    ("bootstrap in a comprehension", "import sys\nfrom utils.a import b\n"
                                     "_ = [sys.path.insert(0,p) for p in ['x']]\n", True),
    ("class nested in a def", "import sys\nfrom utils.a import b\ndef h():\n"
                              "    class C:\n        sys.path.insert(0,'x')\n", True),
    # a genexp body is deferred until consumed; a list comprehension is not
    ("genexp bootstrap is deferred", "import sys\nfrom utils.a import b\n"
                                     "g = (sys.path.insert(0,p) for p in ['x'])\n", True),
    # the callee is resolved against this file's `sys` imports
    ("unrelated .path.append", "import sys\nloader.search_path.append('x')\n"
                               "from utils.a import b\nsys.path.insert(0,'y')\n", True),
    ("aliased sys is recognised", "import sys as s\ns.path.insert(0,'x')\n"
                                  "from utils.a import b\n", False),
    ("aliased sys slice assign", "import sys as s\ns.path[0:0] = ['x']\n"
                                 "from utils.a import b\n", False),
    ("site.addsitedir", "import sys, site\nsite.addsitedir('x')\nfrom utils.a import b\n", False),
    # `if not TYPE_CHECKING:` — the BODY is the branch that runs
    ("not TYPE_CHECKING body runs", "import sys\nfrom typing import TYPE_CHECKING\n"
                                    "if not TYPE_CHECKING:\n    from utils.a import b\n"
                                    "sys.path.insert(0,'x')\n", True),
    ("not TYPE_CHECKING else is typing-only",
     "import sys\nfrom typing import TYPE_CHECKING\nif not TYPE_CHECKING:\n    pass\n"
     "else:\n    from utils.a import b\nsys.path.insert(0,'x')\n", False),
    # ── pass 15: name resolution, not spelling-matching ──────────────
    # first-party TOP-LEVEL MODULES, not just packages
    ("import start above bootstrap",
     "import sys\nimport start\nsys.path.insert(0,'x')\n", True),
    ("from tracks import above bootstrap",
     "import sys\nfrom tracks import load_tracks\nsys.path.insert(0,'x')\n", True),
    # addsitedir resolves its receiver like every other callee
    ("unrelated .addsitedir",
     "import sys\nloader.addsitedir('x')\nfrom utils.a import b\nsys.path.insert(0,'y')\n", True),
    ("from site import addsitedir",
     "import sys\nfrom site import addsitedir\naddsitedir('x')\nfrom utils.a import b\n", False),
    # every spelling that really mutates sys.path
    ("sys.path augmented assign", "import sys\nsys.path += ['x']\nfrom utils.a import b\n", False),
    ("sys.path.extend", "import sys\nsys.path.extend(['x'])\nfrom utils.a import b\n", False),
    ("sys.path rebound", "import sys\nsys.path = ['x']+sys.path\nfrom utils.a import b\n", False),
    ("from sys import path", "from sys import path\npath.insert(0,'x')\nfrom utils.a import b\n", False),
    ("os.sys.path chain", "import os\nos.sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("sys.path index store", "import sys\nsys.path[0] = 'x'\nfrom utils.a import b\n", False),
    # a def HEADER evaluates where it is written; its BODY does not
    ("bootstrap via local helper",
     "import sys\ndef _b(): sys.path.insert(0,'x')\n_b()\nfrom utils.a import b\n", False),
    ("helper that does NOT bootstrap",
     "import sys\ndef _b(): pass\n_b()\nfrom utils.a import b\n", True),
    ("decorator expression",
     "import sys\n@reg(sys.path.insert(0,'x'))\ndef h(): pass\nfrom utils.a import b\n", False),
    ("default-argument expression",
     "import sys\ndef h(_p=sys.path.insert(0,'x')): pass\nfrom utils.a import b\n", False),
    ("class base expression",
     "import sys\nclass C(reg(sys.path.insert(0,'x'))): pass\nfrom utils.a import b\n", False),
    # typing_extensions re-exports the identical flag
    ("typing_extensions TYPE_CHECKING",
     "import sys\nfrom typing_extensions import TYPE_CHECKING\nif TYPE_CHECKING:\n"
     "    from utils.a import b\nsys.path.insert(0,'x')\n", False),
    ("typing_extensions dotted",
     "import sys\nimport typing_extensions as te\nif te.TYPE_CHECKING:\n"
     "    from utils.a import b\nsys.path.insert(0,'x')\n", False),
    # dead code is not a performed bootstrap — counting it short-circuited
    # the scan and every offender below went unreported (a FALSE PASS)
    ("dead: False and insert",
     "import sys\n_ = False and sys.path.insert(0,'x')\nfrom utils.a import b\n", True),
    ("dead: True or insert",
     "import sys\n_ = True or sys.path.insert(0,'x')\nfrom utils.a import b\n", True),
    ("dead: ifexp on a False literal",
     "import sys\n_ = sys.path.insert(0,'x') if False else None\nfrom utils.a import b\n", True),
    # ...but a REAL condition still counts, matching compound statements
    ("live: real condition",
     "import sys\n_ = cfg and sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
    ("live: ifexp on a real condition",
     "import sys\n_ = sys.path.insert(0,'x') if cfg else None\nfrom utils.a import b\n", False),
    ("live: True and insert",
     "import sys\n_ = True and sys.path.insert(0,'x')\nfrom utils.a import b\n", False),
]


@pytest.mark.parametrize("label,source,flagged", _IMPORT_GUARD_CASES,
                         ids=[c[0] for c in _IMPORT_GUARD_CASES])
def test_import_ordering_guard_classifies_known_shapes(label, source, flagged) -> None:
    # Includes top-level MODULES (`start`, `tracks`) alongside packages —
    # `_bootstrapper_packages` derives both, and a package-only fixture here
    # would let a module-name regression pass unnoticed.
    offenders = _first_party_imports_above_bootstrap(
        source, ["utils", "core", "services", "ui", "start", "tracks"]
    )
    assert bool(offenders) is flagged, f"{label}: got {offenders}"


def test_import_ordering_guard_reports_an_unparseable_script() -> None:
    with pytest.raises(AssertionError, match="does not parse"):
        _first_party_imports_above_bootstrap("def broken(\n", ["utils"])


# ── pass 15: writer/reader round-trip ────────────────────────────────


_ROUND_TRIP_VALUES = [
    "plain",
    "",
    "a#b",                 # a hash NOT preceded by whitespace is data
    "ticket #1",           # ` #` starts a comment -> was read back as "ticket"
    "ticket\t#1",          # tab counts as whitespace too
    '"a" IGNORED=b',       # quoted span -> was read back as "a"
    '"abc"',
    "'abc'",
    "abc   ",              # the reader strips
    "   abc",
    "#notacomment",        # -> was read back as ""
    "p@ss w0rd!",
    "a=b=c",
]


@pytest.mark.parametrize("value", _ROUND_TRIP_VALUES, ids=lambda v: repr(v))
def test_a_written_value_is_read_back_unchanged(value, tmp_path):
    """Line-safety alone let the reader silently mangle a secret.

    `assert_safe_env_assignment` only checked that a value could not add a
    LINE. It could still come back DIFFERENT, because the reader strips
    surrounding whitespace, honours quotes, and treats ` #` as a comment. A
    password of `ticket #1` was written verbatim and read back as `ticket` —
    reachable straight from a consumer manifest's `env.values`.
    """
    from core.config_parser import ConfigParser
    from utils.atomic_write import render_env_assignment

    # `render_env_assignment`, not `assert_safe_env_assignment`: the latter
    # VALIDATES and returns the raw value (for parse boundaries that store it),
    # while only a line WRITER renders. Conflating them rendered twice.
    rendered = render_env_assignment("ROUNDTRIP", value)
    (tmp_path / ".env").write_text(f"ROUNDTRIP={rendered}\n", encoding="utf-8")

    parser = ConfigParser(str(tmp_path))
    parser.env_file_path = tmp_path / ".env"
    assert parser.parse_env_file()["ROUNDTRIP"] == value


@pytest.mark.parametrize("value", ["plain", "a#b", "63000", ""])
def test_a_value_needing_no_quoting_stays_bare(value):
    """Quote only what must be quoted — `.env` stays readable."""
    from utils.atomic_write import render_env_assignment

    assert render_env_assignment("K", value) == value


def test_a_value_no_encoding_can_carry_is_refused():
    """Mixing both quote styles with a comment marker has no safe encoding."""
    from utils.atomic_write import assert_safe_env_assignment

    with pytest.raises(ValueError, match="reads it back unchanged"):
        assert_safe_env_assignment("K", "a'b\"c #d")


def test_the_reader_and_the_writer_share_one_decoder():
    """A second copy of the decode rules is how the two drifted apart."""
    import inspect

    from core import config_parser

    source = inspect.getsource(config_parser.ConfigParser.parse_env_file)
    assert "decode_env_value" in source
    # ...and no re-implementation of the quote/comment rules alongside it
    assert "find(quote" not in source
