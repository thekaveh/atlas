#!/usr/bin/env python3
"""Export or verify the generated GitHub Wiki-compatible Atlas docs pages."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI_DIR = ROOT / "docs" / "wiki"
WIKI_HOME = WIKI_DIR / "Home.md"
DEFAULT_WIKI_REMOTE = "https://github.com/thekaveh/atlas.wiki.git"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify docs/wiki/*.md generated from the shared docs model are current.")
    parser.add_argument("--push", action="store_true", help="Push docs/wiki/*.md to the live GitHub Wiki repo.")
    parser.add_argument("--remote", default=DEFAULT_WIKI_REMOTE, help="Git remote for the wiki repository.")
    args = parser.parse_args()

    cmd = [sys.executable, "scripts/generate-docs-site.py"]
    if args.check:
        cmd.append("--check")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode
    if args.push:
        push_wiki(args.remote)
    print(f"wiki export ready: {WIKI_HOME.relative_to(ROOT)}")
    return 0


def _authenticated_remote(remote: str) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or not remote.startswith("https://github.com/"):
        return remote
    return remote.replace("https://github.com/", f"https://x-access-token:{token}@github.com/", 1)


def push_wiki(remote: str) -> None:
    with tempfile.TemporaryDirectory(prefix="atlas-wiki-") as tmp:
        clone_dir = Path(tmp) / "wiki"
        subprocess.run(["git", "clone", _authenticated_remote(remote), str(clone_dir)], check=True)

        for path in clone_dir.glob("*.md"):
            path.unlink()
        for src in sorted(WIKI_DIR.glob("*.md")):
            shutil.copy2(src, clone_dir / src.name)

        subprocess.run(["git", "add", "."], cwd=clone_dir, check=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=clone_dir,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        if not status:
            print("live wiki already current")
            return

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=clone_dir, check=True)
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            cwd=clone_dir,
            check=True,
        )
        subprocess.run(["git", "commit", "-m", "Sync Atlas documentation wiki"], cwd=clone_dir, check=True)
        subprocess.run(["git", "push", "origin", "HEAD:master"], cwd=clone_dir, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
