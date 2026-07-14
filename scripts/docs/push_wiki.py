from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


DEFAULT_REMOTE = "git@github.com:thekaveh/atlas.wiki.git"


def _git_env(key_path: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Atlas Docs Bot")
    env.setdefault("GIT_AUTHOR_EMAIL", "docs@atlas.local")
    env.setdefault("GIT_COMMITTER_NAME", "Atlas Docs Bot")
    env.setdefault("GIT_COMMITTER_EMAIL", "docs@atlas.local")
    if key_path:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        )
    return env


def sync_wiki(source: Path, repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    for item in list(repo_dir.iterdir()):
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in source.iterdir():
        target = repo_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _run_git(args: list[str], *, cwd: Path | None = None, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def push_wiki(source: Path, remote: str, key_path: Path | None, *, push: bool) -> None:
    if push and (remote.startswith("git@") or remote.startswith("ssh://")) and key_path is None:
        raise RuntimeError("WIKI_DEPLOY_KEY must point to a private key for wiki publication")
    env = _git_env(key_path)
    with tempfile.TemporaryDirectory(prefix="atlas-wiki-") as temp:
        repo_dir = Path(temp) / "wiki"
        if push:
            _run_git(["clone", "--depth", "1", remote, str(repo_dir)], env=env)
        else:
            repo_dir.mkdir()
            _run_git(["init"], cwd=repo_dir, env=env)
        sync_wiki(source, repo_dir)
        _run_git(["add", "-A"], cwd=repo_dir, env=env)
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo_dir, env=env, check=False
        )
        if diff.returncode == 0:
            return
        _run_git(["commit", "-m", "docs: synchronize Atlas wiki"], cwd=repo_dir, env=env)
        if push:
            _run_git(["push", "origin", "HEAD:master"], cwd=repo_dir, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the generated Atlas wiki")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--push", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    key_value = os.environ.get("WIKI_DEPLOY_KEY")
    key_path = Path(key_value) if key_value else None
    push_wiki(
        root / "generated" / "wiki",
        os.environ.get("WIKI_REMOTE", DEFAULT_REMOTE),
        key_path,
        push=args.push,
    )


if __name__ == "__main__":
    main()
