import os
import subprocess
from pathlib import Path

from scripts.docs.push_wiki import push_wiki, sync_wiki


def test_sync_wiki_preserves_git_and_removes_stale_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    repo = tmp_path / "repo"
    source.mkdir()
    repo.mkdir()
    (source / "Home.md").write_text("# Home\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / "stale.md").write_text("stale", encoding="utf-8")

    sync_wiki(source, repo)

    assert (repo / ".git").is_dir()
    assert (repo / "Home.md").read_text(encoding="utf-8") == "# Home\n"
    assert not (repo / "stale.md").exists()


def test_push_wiki_uses_master_and_default_ci_identity(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    (source / "Home.md").write_text("# Home\n", encoding="utf-8")
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    for key in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    push_wiki(source, str(remote), key_path=None, push=True)

    author = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "master", "-1", "--format=%an <%ae>"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert author == "Atlas Docs Bot <docs@atlas.local>"
