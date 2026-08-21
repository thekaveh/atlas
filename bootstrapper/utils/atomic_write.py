"""Crash-safe, mode-preserving writes for secrets-bearing text files."""

from __future__ import annotations

import os
import errno
import glob as _glob
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path


def _fsync_parent_directory(path: Path) -> None:
    """Persist a replaced directory entry on platforms that support it."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    unsupported = {
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        directory_fd = os.open(path.parent, flags)
    except OSError as exc:
        if exc.errno in unsupported:
            return
        raise
    try:
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            if exc.errno not in unsupported:
                raise
    finally:
        os.close(directory_fd)


#: A `.env` key is a shell-style identifier. Matched with `fullmatch`, never
#: `match(... "$")`: Python's `$` also matches immediately BEFORE a trailing
#: newline, so `^[A-Za-z_][A-Za-z0-9_]*$` accepts `"KEY\n"` — the single
#: metacharacter this exists to reject.
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


#: Split `.env` text the way its canonical reader does.
#:
#: `ConfigParser.parse_env_file` iterates the file object, so a line ends at
#: `\n` (and at `\r\n`/`\r`, which universal-newline translation has already
#: collapsed to `\n` before this regex sees them — the CR alternatives are kept
#: only so this stays correct if a caller ever reads with `newline=""`).
#:
#: `str.splitlines()` additionally splits on \x0b \x0c \x1c \x1d \x1e \x85
#: U+2028 and U+2029. Any `.env` rewriter that reads with `splitlines()` and
#: re-emits a segment terminated with a real `\n` therefore PROMOTES text after
#: one of those separators into a genuine assignment — which `parse_env_file`
#: then resolves last-wins. That is a privilege-escalation primitive, not a
#: formatting quirk: `stamp_version` runs unattended against every legacy `.env`
#: at start. Use this everywhere `.env` is split, so writer and reader always
#: agree on what a line is.
_ENV_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")


def env_lines(text: str, *, keepends: bool = False) -> list[str]:
    """Line-split `.env` text with the canonical reader's separator set.

    A drop-in for `str.splitlines()` — same output SHAPE, narrower separator
    set. That equivalence is the point: every caller was written against
    `splitlines()`, and `re.split` differs from it in a way that silently
    changes rewrites. `"A=1\n".split` yields a trailing `""` while
    `splitlines()` does not, so an append-at-end rewriter fed the raw split
    emits an extra blank line — measured: all three migrations' `stamp_version`
    grew one, churning every user's `.env` on upgrade. A trailing separator
    therefore does not produce a final empty element here either.
    """
    if not text:
        return []
    parts = _ENV_LINE_SPLIT_RE.split(text)
    ends = _ENV_LINE_SPLIT_RE.findall(text)
    if parts and parts[-1] == "":
        parts = parts[:-1]          # mirror splitlines(): no phantom last line
    if not keepends:
        return parts
    return [seg + (ends[i] if i < len(ends) else "") for i, seg in enumerate(parts)]


# ENV-WRITER SCOPE — which writers `assert_safe_env_assignment` covers — kept out of the function
#: docstring so the function stays readable, and stated precisely because an
#: earlier version of it overclaimed.
#:
#: GUARDED (the four that can carry consumer-supplied data):
#:   AtlasStarter._merge_env_file_overrides, SourceOverrideManager.update_env_file,
#:   ServiceConfigManager.update_env_file, and _set_scalar at the manifest parse
#:   boundary.
#:
#: UNGUARDED (eleven writer functions, all writing internally-generated values):
#:   key_generator.update_env_key, supabase_keys, port_manager,
#:   source_validator._strip_lines_from_env, the backfill splice,
#:   bootstrapper/scripts/reorg_user_env.py, each of the three migrations'
#:   stamp_version AND its body (migration_v1's rewrite, migration_v2's append,
#:   migration_v3's _replace_or_append), and
#:   AtlasStarter._remove_env_keys_by_prefix — which only filters and rejoins
#:   existing lines and formats no assignment at all.
#:
#: Route anything externally-sourced through the guard rather than assuming an
#: unguarded writer is safe. None of the eleven can PROMOTE an embedded separator
#: into a real assignment, for two different reasons checked per call site:
#: those that split `.env` do so with `env_lines`, and the three regex rewriters
#: (key_generator, supabase_keys, port_manager) never split at all — they
#: `re.sub` over whole content, where `^`/`$` under re.MULTILINE anchor only at
#: `\n` and `.` matches the exotic separators rather than terminating on them.

def assert_safe_env_assignment(key: str, value: str) -> str:
    """Reject anything that would emit more than one `.env` assignment.

    Every writer that edits `.env` formats entries as ``KEY=VALUE`` into a
    line-oriented file that ``parse_env_file`` resolves last-wins. A newline on
    EITHER side therefore appends a further assignment that beats the real one
    — and the rewrite patterns those writers use are ``^KEY=.*$`` with
    MULTILINE but not DOTALL, so a later run rewrites only the first line and
    steps over the injected remainder, making it permanent.

    See the ENV-WRITER SCOPE comment above this function for the
    guarded/unguarded split.

    Returns the value coerced to ``str``, **unrendered**. Validation and
    rendering are deliberately separate calls: this one runs at PARSE
    boundaries (a consumer manifest storing into a dict), while
    `render_env_assignment` runs at WRITE boundaries (formatting a `KEY=value`
    line). Folding them together made a manifest value render TWICE —
    `s3cr3t #1` was stored as `"s3cr3t #1"` and then written as
    `'"s3cr3t #1"'`, so `.env` read back the quotes as part of the secret. It
    also aborted startup outright for a value legitimately containing double
    quotes: accepted at the manifest boundary, then rejected by the second
    render, uncaught, straight into the catch-all as "Unexpected error during
    startup".

    Encodability IS still checked here, so an unencodable value fails at
    manifest load with its origin rather than mid-launch with a traceback.
    """
    if not _ENV_KEY_RE.fullmatch(str(key)):
        raise ValueError(
            f"refusing to write {key!r} to .env: not a valid environment "
            f"variable name"
        )
    rendered = str(value)
    # Test against Python's OWN notion of a line, not a `\n`/`\r` membership
    # check. `str.splitlines()` also splits on \x0b \x0c \x1c \x1d \x1e \x85
    # U+2028 and U+2029. Any reader that splits with `splitlines()` therefore
    # sees TWO lines where one was written, and a writer that then rewrites the
    # first segment terminates it with a REAL newline — promoting the remainder
    # to a genuine assignment that wins last-wins resolution. Eight separators
    # bypassed the earlier membership test. The in-tree readers have since been
    # moved onto `env_lines`, which closes that specific route; this check
    # stays because it is the writers' own boundary and must not depend on
    # every present and future reader having been converted.
    #
    # The identity comparison also catches a TRAILING separator, which a
    # membership test over a widened character set would still have missed.
    if rendered.splitlines() != ([rendered] if rendered else []):
        raise ValueError(
            f"refusing to write a multi-line value for {key}: a line separator "
            f"in a .env value injects further assignments"
        )
    render_env_value(key, rendered)  # raises if no encoding round-trips
    return rendered


def render_env_assignment(key: str, value: str) -> str:
    """Validate `value` and render it for emission as ``KEY=<result>``.

    The only entry point a `.env` LINE WRITER should use. Callers that merely
    validate and store (the consumer-manifest parse boundary) must use
    `assert_safe_env_assignment`, or the value renders twice.
    """
    return render_env_value(key, assert_safe_env_assignment(key, value))


def decode_env_value(raw: str) -> str:
    """Decode the right-hand side of one `.env` line, as the reader does.

    THE definition, shared by the reader and by every writer's round-trip
    check. Duplicating it is how a writer came to accept values the reader
    silently mangles.
    """
    value = raw.strip()
    if value[:1] in ('"', "'"):
        # Quoted value: take the quoted span verbatim — a `#` inside quotes is
        # data, not a comment (PASSWORD="ab#cd" used to be read as `ab`).
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value.strip('"').strip("'")  # unterminated quote — legacy cleanup
    # Unquoted: a comment starts only at a hash preceded by whitespace
    # (`ab#cd` is a value; `abc  # note` carries a comment).
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1] in " \t"):
            value = value[:index]
            break
    return value.strip()


def render_env_value(key: str, value: str) -> str:
    """Render `value` so the reader decodes it back to exactly `value`.

    Line-safety alone was not enough. A value the writer happily accepted could
    still come back DIFFERENT, because the reader strips surrounding
    whitespace, honours quotes, and treats ` #` as starting a comment. So a
    password of `s3cr3t #1` was written verbatim and read back as `s3cr3t`, and
    `"a" IGNORED=b` came back as `a` — silent corruption of a secret, reachable
    straight from a consumer manifest's `env.values`.

    Quoting is preferred over rejection: the value is legitimate, only its
    encoding was wrong.
    """
    for candidate in (value, f'"{value}"', f"'{value}'"):
        if decode_env_value(candidate) == value:
            return candidate
    # Contains both quote styles AND needs quoting — no encoding this reader
    # accepts round-trips, so writing it at all would corrupt it.
    raise ValueError(
        f"refusing to write {key}: the value cannot be encoded so that .env "
        f"reads it back unchanged (it mixes single and double quotes with "
        f"whitespace or a comment marker)"
    )


def atomic_write_text(
    destination: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Replace destination only after a complete, flushed temporary write."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = (
        mode
        if mode is not None
        else stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    )
    fd, raw_temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(raw_temporary)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(fd, target_mode)
        else:
            os.chmod(temporary, target_mode)
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            fd = -1
            written = handle.write(content)
            if written != len(content):
                raise OSError(
                    f"short atomic write for {path}: {written}/{len(content)}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_parent_directory(path)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


#: How many snapshots to keep per (source file, version) prefix. Every base-port
#: change, key rotation and env migration drops another full copy of `.env`, so
#: without a cap the repo root accumulates them without bound — each one holding
#: the Supabase JWT signing keys and whatever cloud-provider keys are configured.
#: Keeping the most recent few preserves the rollback the backups exist for while
#: bounding how long a *rotated* secret stays readable on disk.
BACKUP_RETENTION = 5


def _prune_old_backups(
    source_path: Path, prefix: str, *, keep: int, protect: Path | None = None
) -> None:
    """Delete all but the *keep* newest snapshots sharing *prefix*.

    *protect* is the snapshot just written; it is never a prune candidate. The
    timestamp segment has one-second resolution, so a burst of rotations can
    leave several snapshots sharing an mtime — without this, the pruner could
    evict the very backup the caller is about to return.

    Best-effort by design: a backup that cannot be removed (permissions, a
    racing process) must never fail the write that just succeeded.
    """
    if keep < 0:
        return
    # The glob alone is not enough to scope a version. `.env.backup.*` also
    # matches `.env.backup.v1.<ts>.<rand>`, so an UNVERSIONED rotation would
    # treat the v1/v2/v3 migration snapshots as its own history and prune the
    # rollback points those exist to provide. Requiring a timestamp immediately
    # after the prefix keeps each version's history disjoint: after
    # `.env.backup.` a versioned name has `v1.`, not a digit.
    # The regex is escaped, so the glob must be too: ATLAS_ENV_FILE is
    # operator-supplied, and a basename containing a bracket makes
    # `glob(f"{prefix}*")` a character class that matches nothing — silently
    # disabling retention entirely, which is the one failure this must not have.
    scoped = re.compile(re.escape(prefix) + r"\d{8}T\d{6}\.")
    # Compare by NAME, not by path object. `tempfile.mkstemp` always returns an
    # absolute path while `glob` yields paths in whatever form the caller passed
    # in, so `p != protect` is True for every candidate when `source` is
    # relative (or contains `..`) — which puts the just-written snapshot back
    # into the prune set and silently retains one fewer than asked, or at
    # keep=1 deletes the file being returned. Both live in the same directory,
    # so the basename is an exact identity here.
    protect_name = protect.name if protect is not None else None
    try:
        existing = [
            p
            for p in source_path.parent.glob(_glob.escape(prefix) + "*")
            if p.is_file() and p.name != protect_name and scoped.match(p.name)
        ]
    except OSError:
        return
    # `protect` occupies one retention slot.
    keep = max(0, keep - 1)
    # mkstemp's suffix is random, so name order is not age order — stat instead.
    # Several snapshots can share an mtime (the timestamp segment has one-second
    # resolution and a rotation writes them in a burst), so the name breaks ties
    # to keep the choice deterministic rather than glob-order dependent. An entry
    # that vanishes mid-scan sorts oldest and is simply skipped by the unlink.
    def _age(path: Path) -> tuple[float, str]:
        try:
            return (path.stat().st_mtime, path.name)
        except OSError:
            return (0.0, path.name)

    for stale in sorted(existing, key=_age, reverse=True)[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def create_private_backup(
    source: str | Path,
    *,
    version: str | None = None,
    keep: int = BACKUP_RETENTION,
) -> Path:
    """Create a durable, collision-resistant 0600 snapshot of *source*.

    Retains the *keep* most recent snapshots for this source+version prefix and
    prunes older ones. Pass ``keep=-1`` to disable pruning.
    """
    source_path = Path(source)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    version_segment = f".{version}" if version else ""
    prefix = f"{source_path.name}.backup{version_segment}."
    fd, raw_backup = tempfile.mkstemp(
        dir=source_path.parent,
        prefix=f"{prefix}{timestamp}.",
    )
    backup = Path(raw_backup)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(fd, 0o600)
        else:
            os.chmod(backup, 0o600)
        content = source_path.read_bytes()
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            written = handle.write(content)
            if written != len(content):
                raise OSError(
                    f"short backup write for {source_path}: "
                    f"{written}/{len(content)}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent_directory(backup)
        # Prune only after the new snapshot is durable, so a crash mid-write can
        # never leave the caller with fewer backups than it started with.
        _prune_old_backups(source_path, prefix, keep=keep, protect=backup)
        return backup
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    finally:
        if fd >= 0:
            os.close(fd)
