"""Docker-backed harness for the Supabase seed scripts.

Boots the pinned supabase/postgres image, applies every *.sql in a scripts
directory in sorted order (mimicking services/supabase/db/scripts/
db-init-runner.sh), and returns a normalized pg_dump + a seed-row snapshot.

No new Python deps: shells out to the Docker CLI + the psql/pg_dump that ship
inside the image. All psql/pg_dump/pg_isready calls use ``-h 127.0.0.1``
(TCP loopback) rather than the Unix socket, because the supabase/postgres
image's built-in pg_hba.conf requires scram-sha-256 on local socket
connections for supabase_admin but trusts TCP loopback unconditionally.

``python -m tests.seed_harness`` (run from bootstrapper/) regenerates the
committed golden fixtures.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "services" / "supabase" / "db" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_GOLDEN = FIXTURES / "seed_schema_golden.sql"
ROWS_GOLDEN = FIXTURES / "seed_rows_golden.txt"

# Mirrors .env.example SUPABASE_DB_IMAGE / SUPABASE_DB_USER / SUPABASE_DB_NAME.
DB_IMAGE = "supabase/postgres:17.6.1.139"
DB_USER = "supabase_admin"
DB_NAME = "postgres"
IMAGE_PULL_ATTEMPTS = 3
IMAGE_PULL_BACKOFF_SECONDS = 5

# Deterministic seed data lives only in comfyui_workflows (seeded by 12-comfyui.sql).
# Snapshot the columns the seed sets, ordered stably.
SEED_QUERY = (
    "SELECT name, description, category, active "
    "FROM public.comfyui_workflows ORDER BY name;"
)


def docker_available() -> bool:
    """True only when the Docker CLI is on PATH AND the daemon is reachable,
    so the docker-gated tests SKIP (not ERROR) when the daemon is paused."""
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except OSError:
        return False


def ensure_database_image() -> None:
    """Ensure the pinned database image is local, retrying transient pulls.

    Docker's implicit pull inside ``docker run`` is a single registry request.
    CI occasionally loses that request to a registry timeout, so make the
    prerequisite explicit and bounded while preserving the final Docker error.
    """
    inspect = subprocess.run(
        ["docker", "image", "inspect", DB_IMAGE], capture_output=True
    )
    if inspect.returncode == 0:
        return

    final: subprocess.CompletedProcess[bytes] | None = None
    for attempt in range(IMAGE_PULL_ATTEMPTS):
        final = subprocess.run(
            ["docker", "pull", DB_IMAGE], capture_output=True
        )
        if final.returncode == 0:
            return
        if attempt + 1 < IMAGE_PULL_ATTEMPTS:
            time.sleep(IMAGE_PULL_BACKOFF_SECONDS)

    assert final is not None
    raise subprocess.CalledProcessError(
        final.returncode,
        ["docker", "pull", DB_IMAGE],
        output=final.stdout,
        stderr=final.stderr,
    )


def _normalize(dump: str) -> str:
    """Drop pg_dump's comment lines (version banners etc.) and collapse blank
    lines, so the comparison is structural and image-patch-stable.

    Also drops the `\\restrict` / `\\unrestrict` psql meta-commands that
    pg_dump >= 17.5 emits around the dump body: each carries a freshly
    randomized nonce token, so leaving them in would make every dump differ
    from the last (breaking idempotency) and from the golden. They guard
    psql's restricted mode, not schema content."""
    out: list[str] = []
    for line in dump.splitlines():
        if line.startswith("--"):
            continue
        if line.startswith("\\restrict") or line.startswith("\\unrestrict"):
            continue
        if line.strip() == "" and (not out or out[-1] == ""):
            continue
        out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


def run_scripts_and_dump(
    scripts_dir: Path, *, run_twice: bool = False
) -> tuple[str, str]:
    """Return (normalized_schema_dump, seed_rows). Raises CalledProcessError if
    any script exits non-zero (psql -v ON_ERROR_STOP=1)."""
    ensure_database_image()
    name = f"atlas-seedtest-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-e", f"POSTGRES_USER={DB_USER}",
            "-e", "POSTGRES_PASSWORD=postgres",
            "-e", f"POSTGRES_DB={DB_NAME}",
            # Trust auth on TCP loopback (the supabase image's pg_hba.conf mandates
            # scram on the Unix socket, so the harness connects via -h 127.0.0.1).
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            DB_IMAGE,
        ],
        check=True, capture_output=True,
    )
    try:
        for _ in range(180):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1",
                 "-U", DB_USER, "-d", DB_NAME, "-q"]
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("postgres did not become ready in 180s")

        sql_files = sorted(scripts_dir.glob("*.sql"))
        passes = 2 if run_twice else 1
        for _ in range(passes):
            for sql in sql_files:
                with sql.open("rb") as fh:
                    subprocess.run(
                        ["docker", "exec", "-i", name, "psql", "-h", "127.0.0.1",
                         "-v", "ON_ERROR_STOP=1",
                         "-U", DB_USER, "-d", DB_NAME, "-f", "-"],
                        stdin=fh, check=True, capture_output=True,
                    )

        schema = subprocess.run(
            ["docker", "exec", name, "pg_dump", "--schema-only",
             "-h", "127.0.0.1", "-U", DB_USER, "-d", DB_NAME],
            check=True, capture_output=True, text=True,
        ).stdout
        rows = subprocess.run(
            ["docker", "exec", name, "psql", "-h", "127.0.0.1", "-A", "-t",
             "-U", DB_USER, "-d", DB_NAME, "-c", SEED_QUERY],
            check=True, capture_output=True, text=True,
        ).stdout
        return _normalize(schema), rows.strip() + "\n"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    if not docker_available():
        raise SystemExit("docker not on PATH — cannot regenerate goldens")
    schema, rows = run_scripts_and_dump(SCRIPTS_DIR)
    FIXTURES.mkdir(exist_ok=True)
    SCHEMA_GOLDEN.write_text(schema, encoding="utf-8")
    ROWS_GOLDEN.write_text(rows, encoding="utf-8")
    print(f"wrote {SCHEMA_GOLDEN} ({len(schema)} bytes)")
    print(f"wrote {ROWS_GOLDEN} ({len(rows)} bytes)")
