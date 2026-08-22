"""#797: read-only guard against silent Atlas-submodule pin drift.

When a consumer vendors Atlas as a git submodule (``infra/``), the launcher
must never silently advance or stage that pin. This module DETECTS drift
read-only and WARNS loudly; it never runs a mutating git command (no
``checkout`` / ``add`` / ``reset`` / ``stash``) — per the ticket's AC #1 the
launcher "warns, it does not act", and AC #2 it never stages anything.

Drift is detected when EITHER:
  - the submodule's working HEAD != the gitlink the superproject records
    (``git submodule status`` prefixes the entry with ``+``), OR
  - the superproject has a staged/unstaged change to the submodule pointer
    (``git status --porcelain -- <sub>`` is non-empty).

Both signals are produced by read-only ``git`` probes. The guard is a
no-op when Atlas is not a submodule checkout (standalone clone / tarball).
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class SubmodulePinStatus:
    """Result of the read-only drift probe."""

    is_submodule: bool
    superproject_root: Optional[Path]
    submodule_path: Optional[str]
    head_drifted: bool
    staged_in_superproject: bool
    recorded_gitlink: Optional[str]
    working_head: Optional[str]

    @property
    def drifted(self) -> bool:
        return self.is_submodule and (self.head_drifted or self.staged_in_superproject)


def _run_git(args: list[str], cwd: Path, timeout: int = 10) -> Optional[str]:
    """Run a read-only git probe. Returns stripped stdout on success, else None.

    Only ever invoked with read-only subcommands (rev-parse / submodule status /
    status --porcelain); this module performs NO git mutation.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def detect_submodule_pin_drift(atlas_root: Path) -> SubmodulePinStatus:
    """Probe whether ``atlas_root`` is a consumer submodule whose pin drifted.

    Pure read: issues ``git rev-parse`` / ``git submodule status`` /
    ``git status --porcelain`` only. Never mutates the working tree, the
    index, or the superproject.
    """
    atlas_root = Path(atlas_root)

    # rev-parse --show-superproject-working-tree prints the superproject root
    # (exit 0) when CWD is inside an active submodule; empty/non-zero otherwise.
    sp = _run_git(["rev-parse", "--show-superproject-working-tree"], atlas_root)
    if not sp:
        return SubmodulePinStatus(
            is_submodule=False, superproject_root=None, submodule_path=None,
            head_drifted=False, staged_in_superproject=False,
            recorded_gitlink=None, working_head=None,
        )
    superproject = Path(sp)

    # The submodule's path relative to the superproject. NB: git's
    # --show-prefix is relative to the submodule's OWN root (empty when CWD is
    # that root), so derive it from the filesystem relationship instead.
    # Resolve both sides first: on macOS the superproject path git prints uses
    # the canonical /private/var form while a tempfile CWD may be /var/... (a
    # symlink) — lexical relative_to without resolve() raises ValueError there.
    try:
        submodule_path = str(
            atlas_root.resolve().relative_to(superproject.resolve())
        )
    except ValueError:
        # atlas_root is not a descendant of the superproject working tree —
        # not a layout this guard can reason about; treat as no drift.
        return SubmodulePinStatus(
            is_submodule=False, superproject_root=None, submodule_path=None,
            head_drifted=False, staged_in_superproject=False,
            recorded_gitlink=None, working_head=None,
        )

    working_head = _run_git(["rev-parse", "HEAD"], atlas_root)

    # The recorded gitlink = the SHA the superproject's committed tree records
    # for this submodule (`git ls-tree HEAD <path>` → "<mode> commit <sha>\t<path>").
    recorded_gitlink: Optional[str] = None
    if submodule_path:
        ls_line = _run_git(["ls-tree", "HEAD", submodule_path], superproject)
        if ls_line:
            parts = ls_line.split()
            if len(parts) >= 3:
                recorded_gitlink = parts[2]

    # PRIMARY drift signal: the submodule's working HEAD no longer matches the
    # gitlink the superproject has committed. This is the AC #3 invariant
    # (recorded == working) and is robust where `git submodule status`'s '+'
    # flag is not — that flag compares working HEAD against the INDEX, so a
    # consumer `git add infra` (the #797 staging symptom) syncs the index to
    # the new HEAD and makes '+' disappear, masking the drift.
    head_drifted = bool(
        recorded_gitlink is not None
        and working_head is not None
        and recorded_gitlink != working_head
    )

    # Informational: has the superproject STAGED a pointer change? `git status
    # --porcelain` is "XY <path>" — X is the index (staged) column. Only the
    # explicit `git add infra` half of the bug sets X; a bare HEAD drift shows
    # up only in Y (working tree), already covered by head_drifted above.
    staged_in_superproject = False
    if submodule_path:
        porcelain = _run_git(
            ["status", "--porcelain", "--", submodule_path], superproject
        )
        staged_in_superproject = bool(
            porcelain
            and any(line[:1] not in ("", " ", "?") for line in porcelain.splitlines())
        )

    return SubmodulePinStatus(
        is_submodule=True,
        superproject_root=superproject,
        submodule_path=submodule_path,
        head_drifted=head_drifted,
        staged_in_superproject=staged_in_superproject,
        recorded_gitlink=recorded_gitlink,
        working_head=working_head,
    )


_WARN_TEMPLATE = """\
⚠ Atlas submodule pin drift detected (#797) — the launcher did NOT move this.
   The Atlas submodule '{sub}' in {super} no longer matches its recorded gitlink:
     working HEAD : {head}
     recorded pin: {recorded}
   {stage_line}The Atlas launcher never checks out, pulls, or stages the submodule pin
   itself (it only warns); this drift came from outside the launcher — most
   often a consumer wrapper running `git -C {sub} pull` / `git submodule
   update --remote`, or a manual checkout. Re-pin explicitly to keep runs
   reproducible:
       git -C {sub} checkout <your-recorded-pin-or-tag>
   {unstage_hint}Intentionally bumping the pin is fine — just do it explicitly and commit it
   in the consumer superproject.
"""


def warn_if_submodule_pin_drifted(
    atlas_root: Path,
    *,
    sink: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr, flush=True),
) -> bool:
    """Probe for submodule pin drift and warn loudly if detected.

    Returns True when drift was found (and warned about), False otherwise
    (including when Atlas is not a submodule checkout). Never mutates git
    state — satisfies AC #1 (warns, does not act) and AC #2 (never stages).
    """
    status = detect_submodule_pin_drift(Path(atlas_root))
    if not status.drifted:
        return False
    assert status.superproject_root is not None and status.submodule_path is not None
    sub = status.submodule_path
    super_root = status.superproject_root
    stage_line = (
        "The superproject has ALSO staged a pointer change to this submodule.\n   "
        if status.staged_in_superproject else ""
    )
    unstage_hint = (
        f"If that staging was accidental: git -C {super_root} restore --staged {sub}\n   "
        if status.staged_in_superproject else ""
    )
    sink(
        _WARN_TEMPLATE.format(
            sub=sub,
            super=super_root,
            head=(status.working_head or "?")[:12],
            recorded=(status.recorded_gitlink or "?")[:12],
            stage_line=stage_line,
            unstage_hint=unstage_hint,
        )
    )
    return True
