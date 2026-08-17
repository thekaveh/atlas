#!/usr/bin/env python3
"""Install and verify the repository's integrity-locked NLP data assets."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, ContextManager
from urllib.parse import urlsplit
from urllib.request import urlopen


_SCHEMA_KEYS = frozenset({"schema_version", "vader_lexicon"})
_ASSET_KEYS = frozenset({"url", "sha256", "size", "resource", "member", "license"})
_VADER_HOST = "raw.githubusercontent.com"
_VADER_PATH = "/nltk/nltk_data/gh-pages/packages/sentiment/vader_lexicon.zip"
_VADER_RESOURCE = "sentiment/vader_lexicon.zip"
_VADER_MEMBER = "vader_lexicon/vader_lexicon.txt"
_VADER_LICENSE = "MIT License"


@dataclass(frozen=True, slots=True)
class VaderAsset:
    url: str
    sha256: str
    size: int
    resource: PurePosixPath
    member: PurePosixPath
    license: str


class NLPAssetError(RuntimeError):
    """Stable, redacted asset failure."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _fail(category: str) -> NLPAssetError:
    return NLPAssetError(category)


def _safe_posix_path(value: object, *, expected: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise _fail("manifest")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts) or value != expected:
        raise _fail("manifest")
    return PurePosixPath(value)


def load_manifest(path: Path) -> VaderAsset:
    """Load the exact supported manifest shape without leaking malformed input."""

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(raw) != _SCHEMA_KEYS or type(raw["schema_version"]) is not int:
            raise _fail("manifest")
        if raw["schema_version"] != 1:
            raise _fail("manifest")
        table = raw["vader_lexicon"]
        if not isinstance(table, dict) or set(table) != _ASSET_KEYS:
            raise _fail("manifest")

        url = table["url"]
        digest = table["sha256"]
        size = table["size"]
        license_name = table["license"]
        if not isinstance(url, str) or not isinstance(digest, str):
            raise _fail("manifest")
        if type(size) is not int or size <= 0:
            raise _fail("manifest")
        if (
            len(digest) != 64
            or digest.lower() != digest
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise _fail("manifest")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != _VADER_HOST
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != _VADER_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise _fail("manifest")
        if license_name != _VADER_LICENSE:
            raise _fail("manifest")
        return VaderAsset(
            url=url,
            sha256=digest,
            size=size,
            resource=_safe_posix_path(table["resource"], expected=_VADER_RESOURCE),
            member=_safe_posix_path(table["member"], expected=_VADER_MEMBER),
            license=license_name,
        )
    except NLPAssetError:
        raise
    except (KeyError, OSError, TypeError, UnicodeError, ValueError, tomllib.TOMLDecodeError):
        raise _fail("manifest") from None


def default_nltk_data_dir() -> Path:
    """Return NLTK's established per-user default data directory."""

    try:
        from nltk.downloader import Downloader

        path = Path(Downloader().default_download_dir()).expanduser()
    except Exception:
        raise _fail("destination") from None
    if not path.is_absolute():
        raise _fail("destination")
    return path


def _target_path(data_dir: Path, asset: VaderAsset, *, create: bool) -> Path:
    data_dir = data_dir.expanduser()
    if not data_dir.is_absolute() or data_dir.is_symlink():
        raise _fail("destination")
    if create:
        try:
            data_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        except OSError:
            raise _fail("destination") from None
    elif not data_dir.exists():
        return data_dir.joinpath(*asset.resource.parts)
    if not data_dir.is_dir():
        raise _fail("destination")

    parent = data_dir
    for part in asset.resource.parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise _fail("destination")
        if create:
            try:
                parent.mkdir(exist_ok=True, mode=0o755)
            except OSError:
                raise _fail("destination") from None
        if parent.exists() and not parent.is_dir():
            raise _fail("destination")
    target = parent / asset.resource.name
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise _fail("destination")
    return target


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError:
        raise _fail("destination") from None
    return size, digest.hexdigest()


def _validate_archive(path: Path, asset: VaderAsset) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            files = {PurePosixPath(item.filename) for item in archive.infolist() if not item.is_dir()}
            if files != {asset.member}:
                raise _fail("archive")
            archive.read(str(asset.member))
    except NLPAssetError:
        raise
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise _fail("archive") from None


def _smoke(data_dir: Path) -> None:
    try:
        import nltk
        from nltk.sentiment.vader import SentimentIntensityAnalyzer

        original = list(nltk.data.path)
        nltk.data.clear_cache()
        nltk.data.path[:] = [str(data_dir), *(item for item in original if item != str(data_dir))]
        try:
            analyzer = SentimentIntensityAnalyzer()
            if (
                analyzer.polarity_scores("good")["compound"] <= 0
                or analyzer.polarity_scores("bad")["compound"] >= 0
            ):
                raise _fail("smoke")
        finally:
            nltk.data.clear_cache()
            nltk.data.path[:] = original
    except NLPAssetError:
        raise
    except Exception:
        raise _fail("smoke") from None


def verify_vader(asset: VaderAsset, data_dir: Path) -> Path:
    """Verify the installed VADER archive without opening the network."""

    target = _target_path(data_dir, asset, create=False)
    if not target.exists():
        raise _fail("missing")
    size, digest = _digest(target)
    if size != asset.size:
        raise _fail("size")
    if digest != asset.sha256:
        raise _fail("hash")
    _validate_archive(target, asset)
    _smoke(data_dir)
    return target


def _open_url(url: str) -> ContextManager[BinaryIO]:
    return urlopen(url, timeout=30)  # noqa: S310 - exact HTTPS origin is validated above.


def install_vader(
    asset: VaderAsset,
    data_dir: Path,
    *,
    offline: bool,
    opener: Callable[[str], ContextManager[BinaryIO]],
) -> Path:
    """Install a missing verified archive without overwriting existing data."""

    try:
        return verify_vader(asset, data_dir)
    except NLPAssetError as error:
        if error.category != "missing":
            raise
    if offline:
        raise _fail("missing")

    target = _target_path(data_dir, asset, create=True)
    temporary: Path | None = None
    try:
        try:
            response_context = opener(asset.url)
            response = response_context.__enter__()
        except Exception:
            raise _fail("network") from None
        try:
            try:
                output = tempfile.NamedTemporaryFile(
                    mode="w+b",
                    prefix=".vader_lexicon.",
                    dir=target.parent,
                    delete=False,
                )
                temporary = Path(output.name)
            except OSError:
                raise _fail("destination") from None
            with output:
                total = 0
                while True:
                    try:
                        chunk = response.read(64 * 1024)
                    except Exception:
                        raise _fail("network") from None
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > asset.size:
                        raise _fail("size")
                    try:
                        output.write(chunk)
                    except OSError:
                        raise _fail("destination") from None
                if total != asset.size:
                    raise _fail("size")
                try:
                    output.flush()
                    os.fsync(output.fileno())
                except OSError:
                    raise _fail("destination") from None
        finally:
            try:
                response_context.__exit__(None, None, None)
            except Exception:
                pass

        size, digest = _digest(temporary)
        if size != asset.size:
            raise _fail("size")
        if digest != asset.sha256:
            raise _fail("hash")
        _validate_archive(temporary, asset)

        try:
            os.link(temporary, target)
        except FileExistsError:
            return verify_vader(asset, data_dir)
        except OSError:
            raise _fail("destination") from None
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            raise _fail("destination") from None
        return verify_vader(asset, data_dir)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install or verify integrity-locked NLP assets.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    default_manifest = Path(__file__).resolve().parents[1] / "requirements" / "nlp-assets.toml"
    for command in ("install", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--manifest", type=Path, default=default_manifest)
        subparser.add_argument("--data-dir", type=Path)
        if command == "install":
            subparser.add_argument("--offline", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asset = load_manifest(args.manifest)
        data_dir = args.data_dir if args.data_dir is not None else default_nltk_data_dir()
        if args.command == "install":
            install_vader(asset, data_dir, offline=args.offline, opener=_open_url)
            print("nlp asset install passed")
        else:
            verify_vader(asset, data_dir)
            print("nlp asset verification passed")
        return 0
    except NLPAssetError as error:
        print(f"nlp asset operation failed: {error.category}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
