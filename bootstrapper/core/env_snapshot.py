"""The ``.env`` a launch may roll back (#1044).

Every launch writes ``.env`` before it validates or confirms anything:
environment preparation, port migration, source and profile overrides, key
generation. ``EnvSnapshot`` records the file's exact bytes and mode before
the first write, so a launch that fails, is declined, or exits from the
wizard without starting puts it back, and the summary can show what the
launch would change.

Only ``.env`` is covered. A cold start's volume removal and a running stack
the port check stopped are Docker effects, not configuration, and stay done.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.support_bundle import Redactor

#: More changed keys than this are counted, not listed (a first install adds
#: every key in .env.example).
CHANGE_LIST_LIMIT = 40


def _env_values(data: bytes | None) -> dict[str, str]:
    """``KEY=value`` pairs without comments; enough to name what changed."""
    values: dict[str, str] = {}
    for line in (data or b"").decode("utf-8", "replace").splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        key = key.strip()
        if sep and key and not key.startswith("#"):
            values[key] = value.strip()
    return values


@dataclass(frozen=True)
class EnvSnapshot:
    path: Path
    data: bytes | None
    mode: int | None

    @classmethod
    def capture(cls, path: str | Path) -> "EnvSnapshot":
        path = Path(path)
        try:
            return cls(path, path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        except FileNotFoundError:
            return cls(path, None, None)

    def current(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def restore(self) -> bool:
        """Put the captured bytes and mode back, or remove a file that did
        not exist. True when ``.env`` had changed."""
        if self.current() == self.data:
            return False
        if self.data is None:
            self.path.unlink(missing_ok=True)
            return True
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".env.restore-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(self.data)
            os.chmod(temp, self.mode if self.mode is not None else 0o600)
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        return True

    def changes(self) -> list[str]:
        """One line per added (+), changed (~) or removed (-) key.

        Values of secret-named keys are never shown, and any secret value
        embedded in another value (a URI with a password) is scrubbed."""
        before, after = _env_values(self.data), _env_values(self.current())
        redactor = Redactor(before, after)

        def shown(key: str, value: str) -> str:
            return str(redactor.value(value, key))

        lines = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before:
                lines.append(f"+ {key}={shown(key, after[key])}")
            elif key not in after:
                lines.append(f"- {key}")
            elif before[key] != after[key]:
                lines.append(f"~ {key}: {shown(key, before[key])} -> {shown(key, after[key])}")
        return lines

    def summary_lines(self) -> list[str]:
        """What the summary prints: the change list, or a count when long."""
        if self.data is None:
            return [f".env will be created ({len(_env_values(self.current()))} keys)."]
        changes = self.changes()
        if not changes:
            return [".env is unchanged."]
        if len(changes) > CHANGE_LIST_LIMIT:
            return [f".env: {len(changes)} keys change (more than {CHANGE_LIST_LIMIT}; not listed)."]
        return [f".env: {len(changes)} key(s) change:", *(f"  {line}" for line in changes)]
