"""Guards for #568 — ComfyUI runtime manifests must never dirty the checkout.

``volumes/comfyui/selected-models.yaml``, ``active-models.tsv``, and
``active-custom-nodes.tsv`` are rewritten by the bootstrapper on every
enabled start. They were accidentally committed once (in 6b751672), which
made every legitimate start dirty the Atlas checkout — and, for consumers
vendoring Atlas as a submodule, fail their own submodule-cleanliness
gates. These tests pin the fix: the outputs are gitignored and untracked,
while the tracked marker files keep the directory itself present on fresh
clones (the always-on backend bind-mounts ``volumes/comfyui/``).
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

RUNTIME_OUTPUTS = (
    "volumes/comfyui/selected-models.yaml",
    "volumes/comfyui/active-models.tsv",
    "volumes/comfyui/active-custom-nodes.tsv",
)

MARKERS = (
    "volumes/comfyui/.gitkeep",
    "volumes/comfyui/README.md",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("git not available")


def _require_git_checkout() -> None:
    result = _git("rev-parse", "--show-toplevel")
    if result.returncode != 0:
        pytest.skip("not running from a git checkout")
    # A .git-less Atlas tree (tarball / ZIP download) nested inside some
    # OTHER repo would resolve to that repo's toplevel — git answers would
    # describe the wrong index, so skip rather than fail spuriously.
    if Path(result.stdout.strip()).resolve() != REPO_ROOT.resolve():
        pytest.skip("Atlas tree is not the git toplevel (tarball inside another repo)")


def test_runtime_outputs_are_gitignored():
    _require_git_checkout()
    for path in RUNTIME_OUTPUTS:
        assert _git("check-ignore", "--quiet", path).returncode == 0, (
            f"{path} is not gitignored — a normal start would dirty the "
            f"checkout (and consumer submodules) again (#568)"
        )


def test_runtime_outputs_are_not_tracked():
    _require_git_checkout()
    result = _git("ls-files", "volumes/")
    assert result.returncode == 0
    tracked = set(result.stdout.split())
    leaked = tracked.intersection(RUNTIME_OUTPUTS)
    assert not leaked, (
        f"runtime outputs tracked in git: {sorted(leaked)} — these are "
        f"rewritten on every enabled start and must stay untracked (#568)"
    )
    missing_markers = set(MARKERS) - tracked
    assert not missing_markers, (
        f"tracked marker files missing: {sorted(missing_markers)} — the "
        f"always-on backend bind-mounts volumes/comfyui/, so the directory "
        f"must exist on a fresh clone (Docker would auto-create it "
        f"root-owned on rootful Linux daemons)"
    )


def test_gitignore_uses_narrow_file_patterns():
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    lines = {line.strip() for line in gitignore.splitlines()}
    for path in RUNTIME_OUTPUTS:
        assert path in lines, f".gitignore is missing the narrow entry {path}"
    # The runtime-artifacts block deliberately avoids directory globs so
    # hand-checked files (like the tracked markers) stay visible.
    assert "volumes/comfyui/" not in lines
    assert "volumes/comfyui/*" not in lines


def test_disabled_source_still_creates_manifest_dir(tmp_path):
    """COMFYUI_SOURCE=disabled writes nothing but must still mkdir the
    manifest directory: the backend mounts it unconditionally, and a
    Docker-auto-created host dir is root-owned on rootful Linux."""
    (tmp_path / ".env").write_text("COMFYUI_SOURCE=disabled\n")
    from start import AtlasStarter

    starter = AtlasStarter()
    starter.root_dir = tmp_path
    starter.config_parser.env_file_path = tmp_path / ".env"

    assert starter.generate_comfyui_manifest() is True
    manifest_dir = tmp_path / "volumes" / "comfyui"
    assert manifest_dir.is_dir()
    assert list(manifest_dir.iterdir()) == []  # nothing generated
