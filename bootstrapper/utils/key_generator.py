"""
Encryption key generation utilities for Atlas services.

Generates N8N_ENCRYPTION_KEY and SEARXNG_SECRET for secure operation.
"""

import base64
import secrets
import re
from pathlib import Path
from typing import Optional, Dict

from core.config_parser import ConfigParser


def _cli_safe_token_urlsafe(nbytes: int) -> str:
    """secrets.token_urlsafe() with a guard: never return a value whose
    first character is `-` or `_`.

    secrets.token_urlsafe() emits the URL-safe Base64 alphabet
    `[A-Za-z0-9_-]`, so ~3% of values start with one of those two chars.
    Generated secrets get passed verbatim to argparse-based CLIs
    (`airflow users create --password ${VAR}`, `airflow connections add
    --conn-password ${VAR}`, etc.), and argparse interprets the leading
    `-` as another flag → `argument -p/--password: expected one argument`.
    Init script crashes; airflow-init exits non-zero; the entire airflow
    family fails to start.

    The init script ALSO uses `--flag=VALUE` (= form) defensively, but
    this bootstrapper-side guard keeps any future CLI consumer safe
    without having to remember the argparse trap. Re-roll until the
    first character is in `[A-Za-z0-9]`.
    """
    while True:
        candidate = secrets.token_urlsafe(nbytes)
        if candidate and candidate[0] not in ("-", "_"):
            return candidate


class KeyGenerator:
    """Generates and manages encryption keys for Atlas services."""

    MINIO_CONSUMERS = ("COMFYUI", "BACKEND", "N8N", "JUPYTER", "DOCLING", "ICEBERG")

    # `.env.example` ships placeholders for credential vars whose canonical
    # generators rotate-when-absent only. Treating these literal placeholders
    # as "absent" so the rotator runs on first ./start.sh stops the stack
    # from booting with a publicly-known, repo-committed credential.
    # Hand-supplied real values stay untouched.
    PLACEHOLDER_DEFAULTS = {
        "N8N_ENCRYPTION_KEY": "your-random-encryption-key",
        "SUPABASE_DB_PASSWORD": "password",
        "SUPABASE_DB_APP_PASSWORD": "app_password",
        "GRAPH_DB_PASSWORD": "neo4j_password",
        # GRAPH_DB_AUTH is the composite form (user/password) passed verbatim to
        # NEO4J_AUTH in compose.yml. It derives from GRAPH_DB_PASSWORD and ships
        # the same well-known placeholder value; generate_and_update_graph_db_password
        # rewrites it as a side effect so it is rotation-covered.
        "GRAPH_DB_AUTH": "neo4j/neo4j_password",
        "REDIS_PASSWORD": "redis_password",
        "DASHBOARD_PASSWORD": "kong_password",
        "OPEN_WEB_UI_ADMIN_PASSWORD": "admin",
        "OPEN_WEB_UI_SECRET_KEY": "secret",
    }

    def _read_env_values(self) -> dict:
        """Return a dict of all KEY=VALUE pairs from the active .env file.

        Uses ConfigParser.parse_env_file() so quote / inline-comment
        semantics stay identical to every other .env reader in the
        bootstrapper.  Falls back to reading self.root_dir directly when
        it points at the .env file itself (i.e. the caller passed a file
        path rather than a directory — used in tests).
        """
        target = self.env_file_path
        if not target.exists() and self.root_dir.is_file():
            # Called as KeyGenerator(str(env_file)) — root_dir IS the file.
            target = self.root_dir
        if not target.exists():
            return {}
        try:
            cp = ConfigParser(str(self.root_dir))
            cp.env_file_path = target
            return cp.parse_env_file()
        except Exception as e:
            raise RuntimeError(
                f"cannot read .env to verify secrets: {e}"
            ) from e

    def _is_placeholder_or_empty(self, var_name: str) -> bool:
        """Return True if .env carries either no value for `var_name` or the
        literal placeholder shipped in `.env.example`. Used by every
        rotate-when-absent generator to also rotate the known placeholder.
        """
        current = self.get_current_env_value(var_name)
        if not current:
            return True
        return current == self.PLACEHOLDER_DEFAULTS.get(var_name)

    def assert_no_placeholders_remaining(self) -> None:
        """Raise if any managed secret in .env still equals its shipped
        placeholder literal. Used as a prod-launch gate.

        Reads all .env values in one pass via _read_env_values() so
        inline-comment and quote semantics are handled correctly. Only
        vars listed in PLACEHOLDER_DEFAULTS are checked — generated-when-
        absent vars (LITELLM_MASTER_KEY, Airflow secrets, MinIO keys, etc.)
        are intentionally excluded because they have no shipped placeholder.
        """
        values = self._read_env_values()
        offenders = [
            name for name, placeholder in self.PLACEHOLDER_DEFAULTS.items()
            if (values.get(name, "") or "") == placeholder
        ]
        if offenders:
            raise RuntimeError(
                "Refusing to launch: placeholder secrets not rotated: "
                + ", ".join(sorted(offenders))
                + ". Run ./start.sh once (auto-rotates) or set strong values in .env."
            )

    def __init__(self, root_dir: Optional[str] = None):
        """
        Initialize key generator.
        
        Args:
            root_dir: Root directory containing .env file
        """
        if root_dir is None:
            # Default to parent directory of bootstrapper
            self.root_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.root_dir = Path(root_dir)

        # Resolve through ConfigParser so ATLAS_ENV_FILE is honored —
        # hardcoding root/.env wrote every generated secret to the wrong
        # file when a custom env path was in use (SupabaseKeyGenerator
        # already resolves this way).
        self.env_file_path = ConfigParser(str(self.root_dir)).env_file_path
    
    def generate_n8n_encryption_key(self) -> str:
        """
        Generate N8N encryption key (48 character hex string).
        Equivalent to: openssl rand -hex 24
        
        Returns:
            str: 48-character hex string
        """
        return secrets.token_hex(24)
    
    def generate_searxng_secret(self) -> str:
        """
        Generate SearxNG secret key (64 character hex string).
        Equivalent to: openssl rand -hex 32

        Returns:
            str: 64-character hex string
        """
        return secrets.token_hex(32)

    def generate_litellm_master_key(self) -> str:
        """LiteLLM master key — must start with `sk-` per LiteLLM's contract."""
        return f"sk-{_cli_safe_token_urlsafe(40)}"

    def generate_minio_root_password(self) -> str:
        """MinIO root password — 32-char URL-safe random."""
        return _cli_safe_token_urlsafe(24)

    def generate_minio_access_key(self) -> str:
        """MinIO service-account access key — 20-char uppercase alphanumeric (S3 convention)."""
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(secrets.choice(alphabet) for _ in range(20))

    def generate_minio_secret_key(self) -> str:
        """MinIO service-account secret key — 40-char URL-safe random."""
        return _cli_safe_token_urlsafe(30)

    def get_current_env_value(self, key_name: str) -> Optional[str]:
        """
        Get current value of an environment variable from .env file.
        
        Args:
            key_name: Name of the environment variable
            
        Returns:
            str: Current value, or None if not found/empty
        """
        if not self.env_file_path.exists():
            return None

        try:
            # Delegate to ConfigParser so quote / inline-comment semantics
            # stay identical to every other .env reader. The old local
            # regex captured the whole rest-of-line: a blank value with an
            # inline comment (`KEY=   # note`) read back as "# note", and
            # inline comments stayed inside values — both broke the
            # rotate-when-absent placeholder comparisons.
            cp = ConfigParser(str(self.root_dir))
            cp.env_file_path = self.env_file_path
            value = cp.parse_env_file().get(key_name, '')
        except Exception:
            return None

        return value if value else None
    
    def update_env_key(self, key_name: str, key_value: str, create_backup: bool = False) -> bool:
        """
        Update or add a key in the .env file.
        
        Args:
            key_name: Name of the environment variable
            key_value: Value to set
            create_backup: Whether to create a backup before modifying
            
        Returns:
            bool: True if successful
        """
        if not self.env_file_path.exists():
            print(f"❌ .env file not found: {self.env_file_path}")
            return False
        
        try:
            # Create backup if requested
            if create_backup:
                import datetime
                timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
                backup_path = self.root_dir / f".env.backup.{timestamp}"
                import shutil
                shutil.copy2(self.env_file_path, backup_path)

            # Read current content
            with open(self.env_file_path, 'r', encoding="utf-8") as f:
                content = f.read()
            
            # Check if key already exists
            pattern = rf'^{re.escape(key_name)}=.*$'
            if re.search(pattern, content, re.MULTILINE):
                # Replace existing key. Lambda form so re.sub doesn't
                # interpret backslash sequences in the value (same guard
                # as SourceOverrideManager.update_env_file) — current
                # generators emit [A-Za-z0-9_-] only, but keep the seam
                # corruption-proof for future value shapes.
                replacement = f'{key_name}={key_value}'
                updated_content = re.sub(
                    pattern, lambda _m, r=replacement: r, content,
                    flags=re.MULTILINE,
                )
            else:
                # Add new key at the end
                updated_content = content
                if not content.endswith('\n'):
                    updated_content += '\n'
                updated_content += f'{key_name}={key_value}\n'
            
            # Atomic, mode-preserving write (tmp + os.replace) — mirrors
            # SourceOverrideManager; an in-place 'w' truncates the
            # secrets-bearing .env on a crash mid-write.
            import os as _os
            tmp_path = Path(str(self.env_file_path) + '.tmp')
            try:
                original_mode = _os.stat(self.env_file_path).st_mode
                with open(tmp_path, 'w', encoding="utf-8") as f:
                    _os.chmod(tmp_path, original_mode)
                    f.write(updated_content)
                _os.replace(tmp_path, self.env_file_path)
            finally:
                tmp_path.unlink(missing_ok=True)
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to update {key_name} in .env file: {e}")
            return False
    
    def generate_and_update_n8n_key(self, force: bool = False) -> bool:
        """
        Generate and update N8N_ENCRYPTION_KEY in .env file.

        Rotates when absent OR when the shipped placeholder
        ``"your-random-encryption-key"`` is still present. Any other
        operator-supplied value sticks — rotating the encryption key
        mid-run makes every previously-saved n8n credential unreadable.

        Args:
            force: Generate new key even if one already exists.

        Returns:
            bool: True if successful
        """
        if not force and not self._is_placeholder_or_empty('N8N_ENCRYPTION_KEY'):
            return True

        new_key = self.generate_n8n_encryption_key()
        return bool(self.update_env_key('N8N_ENCRYPTION_KEY', new_key))
    
    def generate_and_update_searxng_secret(self, force: bool = False) -> bool:
        """
        Generate and update SEARXNG_SECRET in .env file.
        
        Args:
            force: Generate new key even if one already exists
            
        Returns:
            bool: True if successful
        """
        current_value = self.get_current_env_value('SEARXNG_SECRET')
        
        if not force and current_value:
            return True

        new_secret = self.generate_searxng_secret()
        return bool(self.update_env_key('SEARXNG_SECRET', new_secret))
    
    def generate_and_update_litellm_master_key(self, force: bool = False) -> bool:
        """Generate LITELLM_MASTER_KEY when absent. Idempotent: never overwrites
        an existing key (preserves virtual-key + spend history) unless force=True.
        """
        current_value = self.get_current_env_value('LITELLM_MASTER_KEY')
        if not force and current_value:
            return True
        new_key = self.generate_litellm_master_key()
        return self.update_env_key('LITELLM_MASTER_KEY', new_key)

    def generate_hermes_api_key(self) -> str:
        """Hermes API server bearer key. Hermes requires >= 8 chars but doesn't
        prescribe a format; a URL-safe 32-byte token is consistent with
        LITELLM_MASTER_KEY's strength (no `sk-` prefix — Hermes has no contract
        around it).
        """
        return _cli_safe_token_urlsafe(32)

    def generate_and_update_hermes_api_key(self, force: bool = False) -> bool:
        """Generate HERMES_API_KEY when absent. Idempotent: existing keys are
        preserved so already-running Hermes sessions / saved Open WebUI client
        config keep working across re-runs.
        """
        current_value = self.get_current_env_value('HERMES_API_KEY')
        if not force and current_value:
            return True
        new_key = self.generate_hermes_api_key()
        return self.update_env_key('HERMES_API_KEY', new_key)

    def generate_lightrag_api_key(self) -> str:
        """Bearer key for LightRAG /api endpoints. Forwarded to LiteLLM."""
        return f"sk-lightrag-{_cli_safe_token_urlsafe(32)}"

    def generate_and_update_lightrag_api_key(self, force: bool = False) -> bool:
        """Generate LIGHTRAG_API_KEY when absent. Idempotent: existing keys are
        preserved so already-running LightRAG requests keep working across re-runs.
        """
        current_value = self.get_current_env_value('LIGHTRAG_API_KEY')
        if not force and current_value:
            return True
        new_key = self.generate_lightrag_api_key()
        return self.update_env_key('LIGHTRAG_API_KEY', new_key)

    def generate_lightrag_token_secret(self) -> str:
        """JWT signing secret for LightRAG /login flows. Without it, LightRAG
        emits a TOKEN_SECRET warning on every boot and falls back to a
        hardcoded default key (real security risk in any non-trivial deploy).
        """
        return _cli_safe_token_urlsafe(48)

    def generate_and_update_lightrag_token_secret(self, force: bool = False) -> bool:
        """Generate LIGHTRAG_TOKEN_SECRET when absent. Idempotent."""
        current_value = self.get_current_env_value('LIGHTRAG_TOKEN_SECRET')
        if not force and current_value:
            return True
        new_key = self.generate_lightrag_token_secret()
        return self.update_env_key('LIGHTRAG_TOKEN_SECRET', new_key)

    def generate_webui_secret_key(self) -> str:
        """Open WebUI JWT/session signing key. Used by Open WebUI itself
        AND by ``services/open-webui/init/scripts/register-{tools,functions}.py``
        to sign admin-token JWTs via ``jwt.encode(..., algorithm="HS256")``.
        PyJWT 2.10+ logs ``InsecureKeyLengthWarning`` for keys shorter
        than 32 bytes on HS256 (RFC 7518 Section 3.2), so we emit a
        URL-safe 32-byte token — same posture as LITELLM_MASTER_KEY /
        HERMES_API_KEY.
        """
        return _cli_safe_token_urlsafe(32)

    def generate_and_update_webui_secret_key(self, force: bool = False) -> bool:
        """Generate OPEN_WEB_UI_SECRET_KEY when absent OR when the shipped
        placeholder ``"secret"`` (6 bytes literally) is still in place.
        Idempotent for any other user-supplied value: hand-edits stick.
        Rotating mid-run signs everyone out of Open WebUI, so we only
        upgrade the placeholder, never a real key.
        """
        if not force and not self._is_placeholder_or_empty('OPEN_WEB_UI_SECRET_KEY'):
            return True
        new_key = self.generate_webui_secret_key()
        return self.update_env_key('OPEN_WEB_UI_SECRET_KEY', new_key)

    # ─── Infrastructure password placeholders ──────────────────────────
    # SUPABASE_DB_PASSWORD, GRAPH_DB_PASSWORD, REDIS_PASSWORD,
    # DASHBOARD_PASSWORD (Kong), and OPEN_WEB_UI_ADMIN_PASSWORD all ship
    # well-known placeholder values in `.env.example`. Without rotation,
    # `cp .env.example .env && ./start.sh` boots the stack with
    # repo-committed credentials. Each rotator upgrades the placeholder
    # only — hand-supplied values stick (rotating mid-run is destructive:
    # the existing database/role/user already authenticates against the
    # current value, and post-init container state diverges).

    def generate_password(self, nbytes: int = 24) -> str:
        """Generic infrastructure-credential generator. URL-safe, CLI-safe."""
        return _cli_safe_token_urlsafe(nbytes)

    def _project_volume_exists(self, suffixes: set[str]) -> bool:
        """Best-effort: True when any candidate project-scoped volume exists."""
        project = (self.get_current_env_value("PROJECT_NAME") or "").strip()
        if not project:
            return False
        candidates = {f"{project}{suffix}" for suffix in suffixes}
        try:
            import subprocess  # local import: keep module import surface small
            result = subprocess.run(
                ["docker", "volume", "ls", "--format", "{{.Name}}"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                return False
            names = {ln.strip() for ln in result.stdout.splitlines() if ln.strip()}
            return bool(candidates & names)
        except Exception:
            return False

    def _supabase_db_volume_exists(self) -> bool:
        """Best-effort: True when THIS project's Postgres data volume already
        exists.

        The `supabase_admin` / app role passwords are baked into the
        `supabase-db-data` volume at `initdb` and never re-synced. If the
        volume exists, rotating the `.env` placeholder to a fresh random value
        would make every client authenticate with a value the role does not
        have → ``password authentication failed for user "supabase_admin"``.

        Scoped to the project's volume name (``<project>_supabase-db-data``,
        Compose's default underscore form; hyphen accepted too) so an
        unrelated project's volume never trips it. Returns False on ANY
        uncertainty — no PROJECT_NAME, docker missing/not running, non-zero
        exit, timeout — so a genuine first run is never blocked from rotating.
        """
        return self._project_volume_exists({"_supabase-db-data", "-supabase-db-data"})

    def _neo4j_db_volume_exists(self) -> bool:
        """Best-effort: True when THIS project's Neo4j data volume exists.

        Neo4j applies `NEO4J_AUTH` on first boot. If the graph data volume
        already exists, rotating `GRAPH_DB_PASSWORD` in `.env` would desync
        clients from the persisted database password.
        """
        return self._project_volume_exists({"_graph-db-data", "-graph-db-data"})

    def _warn_db_password_locked(self, var_name: str, service_name: str) -> None:
        """Warn that a placeholder DB password can't be safely rotated because
        the data volume (which baked the role password at initdb) already
        exists."""
        project = (self.get_current_env_value("PROJECT_NAME") or "").strip()
        print(
            f"⚠ {var_name} is still the placeholder, but the {service_name} data volume "
            f"for project '{project}' already exists.\n"
            f"   The DB role password was baked into that volume at initdb and cannot "
            f"be changed by rewriting .env, so rotation is skipped.\n"
            f"   If startup fails with 'password authentication failed', set {var_name} "
            f"in .env to the volume's original password, or run ./stop.sh --cold to "
            f"start fresh."
        )

    def generate_and_update_supabase_db_password(self, force: bool = False) -> bool:
        """Rotate `SUPABASE_DB_PASSWORD` (Postgres `supabase_admin` role) only
        when absent or still the `password` placeholder. The value is baked
        into Postgres at `initdb` time; rotating after the cluster initialises
        would not change the stored role password.

        Guard: if THIS project's `supabase-db-data` volume already exists, the
        role password is frozen at initdb — rotating the placeholder here would
        guarantee an auth mismatch, so skip + warn instead (see
        _supabase_db_volume_exists).
        """
        if not force and not self._is_placeholder_or_empty('SUPABASE_DB_PASSWORD'):
            return True
        if not force and self._supabase_db_volume_exists():
            self._warn_db_password_locked('SUPABASE_DB_PASSWORD', 'Postgres')
            return True
        return self.update_env_key('SUPABASE_DB_PASSWORD', self.generate_password())

    def generate_and_update_supabase_db_app_password(self, force: bool = False) -> bool:
        """Rotate `SUPABASE_DB_APP_PASSWORD` (application role) only when absent
        or still the `app_password` placeholder. Same `initdb`-bound semantics
        as the admin role — including the existing-volume guard.
        """
        if not force and not self._is_placeholder_or_empty('SUPABASE_DB_APP_PASSWORD'):
            return True
        if not force and self._supabase_db_volume_exists():
            self._warn_db_password_locked('SUPABASE_DB_APP_PASSWORD', 'Postgres')
            return True
        return self.update_env_key('SUPABASE_DB_APP_PASSWORD', self.generate_password())

    def generate_and_update_graph_db_password(self, force: bool = False) -> bool:
        """Rotate `GRAPH_DB_PASSWORD` (Neo4j) only when absent or still the
        `neo4j_password` placeholder. Also rewrites the composite
        `GRAPH_DB_AUTH=neo4j/<password>` since `services/neo4j/compose.yml`
        passes that literal through to `NEO4J_AUTH`. Neo4j sets the password
        from `NEO4J_AUTH` only at first boot — rotating after the database
        initialises requires `ALTER USER ... SET PASSWORD` in cypher-shell.
        """
        if not force and not self._is_placeholder_or_empty('GRAPH_DB_PASSWORD'):
            return True
        if not force and self._neo4j_db_volume_exists():
            self._warn_db_password_locked('GRAPH_DB_PASSWORD', 'Neo4j')
            return True
        new_pw = self.generate_password()
        if not self.update_env_key('GRAPH_DB_PASSWORD', new_pw):
            return False
        graph_user = self.get_current_env_value('GRAPH_DB_USER') or 'neo4j'
        return self.update_env_key('GRAPH_DB_AUTH', f'{graph_user}/{new_pw}')

    def generate_and_update_redis_password(self, force: bool = False) -> bool:
        """Rotate `REDIS_PASSWORD` only when absent or still the `redis_password`
        placeholder. `REDIS_URL` is a `${REDIS_PASSWORD}`-interpolated
        compose-time expansion so no separate sync is required. Rotating a
        live Redis without restart-with-`--requirepass` breaks every active
        consumer connection mid-flight.
        """
        if not force and not self._is_placeholder_or_empty('REDIS_PASSWORD'):
            return True
        return self.update_env_key('REDIS_PASSWORD', self.generate_password())

    def generate_and_update_kong_dashboard_password(self, force: bool = False) -> bool:
        """Rotate Kong's `DASHBOARD_PASSWORD` only when absent or still the
        `kong_password` placeholder. Read by `KongConfigGenerator` at
        `volumes/api/kong-dynamic.yml` render time, so a placeholder upgrade
        propagates on the next `./start.sh`.
        """
        if not force and not self._is_placeholder_or_empty('DASHBOARD_PASSWORD'):
            return True
        return self.update_env_key('DASHBOARD_PASSWORD', self.generate_password())

    def generate_and_update_webui_admin_password(self, force: bool = False) -> bool:
        """Rotate `OPEN_WEB_UI_ADMIN_PASSWORD` only when absent or still the
        `admin` placeholder. The open-webui-init container re-registers the
        admin user on every boot via the Open WebUI signup API, so a
        placeholder upgrade applies on the next `./start.sh`.
        """
        if not force and not self._is_placeholder_or_empty('OPEN_WEB_UI_ADMIN_PASSWORD'):
            return True
        return self.update_env_key('OPEN_WEB_UI_ADMIN_PASSWORD', self.generate_password())

    def generate_grafana_admin_password(self) -> str:
        """Grafana admin password — 32-char URL-safe random. Mirrors the
        LiteLLM master-key strength. Persisted to .env on first run; rotating
        is a deliberate operator action.
        """
        return _cli_safe_token_urlsafe(24)

    def generate_and_update_grafana_admin_password(self, force: bool = False) -> bool:
        """Generate GRAFANA_ADMIN_PASSWORD when absent. Idempotent: hand-edits
        stick. Rotating mid-run signs everyone out of Grafana, so we never
        force-overwrite a real value.
        """
        current_value = self.get_current_env_value('GRAFANA_ADMIN_PASSWORD')
        if not force and current_value:
            return True
        new_value = self.generate_grafana_admin_password()
        return self.update_env_key('GRAFANA_ADMIN_PASSWORD', new_value)

    def generate_and_update_minio_root_password(self, force: bool = False) -> bool:
        """Generate MINIO_ROOT_PASSWORD when absent. Hand-edits stick unless force=True."""
        current_value = self.get_current_env_value('MINIO_ROOT_PASSWORD')
        if not force and current_value:
            return True
        new_value = self.generate_minio_root_password()
        return self.update_env_key('MINIO_ROOT_PASSWORD', new_value)

    def generate_and_update_minio_consumer_keys(self, force: bool = False) -> Dict[str, bool]:
        """Generate MINIO_<NAME>_ACCESS_KEY + MINIO_<NAME>_SECRET_KEY for every consumer in
        MINIO_CONSUMERS, only when blank. Returns a per-variable success map.
        """
        results: Dict[str, bool] = {}
        for consumer in self.MINIO_CONSUMERS:
            access_var = f'MINIO_{consumer}_ACCESS_KEY'
            secret_var = f'MINIO_{consumer}_SECRET_KEY'

            if force or not self.get_current_env_value(access_var):
                results[access_var] = self.update_env_key(access_var, self.generate_minio_access_key())
            else:
                results[access_var] = True

            if force or not self.get_current_env_value(secret_var):
                results[secret_var] = self.update_env_key(secret_var, self.generate_minio_secret_key())
            else:
                results[secret_var] = True

        return results

    # ─── Airflow secrets ───────────────────────────────────────────────
    # Airflow 3.x requires four bootstrapper-generated secrets on first
    # run: Fernet (Connection-password encryption), AIRFLOW__API__SECRET_KEY
    # (signs inter-process payloads — was [webserver] secret_key in 2.x),
    # admin login password, and the dedicated airflow Postgres role
    # password. None should rotate mid-run — Fernet rotation invalidates
    # every stored Connection password; admin/role rotation breaks UI
    # login + scheduler DB access.

    def generate_airflow_fernet_key(self) -> str:
        """32-byte URL-safe base64 (Fernet's required format)."""
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

    def generate_airflow_secret_key(self) -> str:
        """AIRFLOW__API__SECRET_KEY (Airflow 3.x). Signs inter-process payloads
        (DagFileProcessor → scheduler RPC, deferrable triggers, multi-scheduler
        JWTs). Was [webserver] secret_key + Flask session in 2.x. 32 chars."""
        return _cli_safe_token_urlsafe(32)

    def generate_airflow_admin_password(self) -> str:
        """Admin login password. 24 chars URL-safe random."""
        return _cli_safe_token_urlsafe(18)

    def generate_jenkins_admin_password(self) -> str:
        """Jenkins admin login password. 24 chars URL-safe random."""
        return _cli_safe_token_urlsafe(18)

    def generate_airflow_db_password(self) -> str:
        """Postgres role password for the dedicated airflow role/database."""
        return _cli_safe_token_urlsafe(24)

    def generate_iceberg_db_password(self) -> str:
        """Postgres role password for the dedicated Iceberg catalog database."""
        return _cli_safe_token_urlsafe(24)

    def generate_and_update_airflow_fernet_key(self, force: bool = False) -> bool:
        """Generate and update AIRFLOW_FERNET_KEY in .env file.

        Args:
            force: Generate new key even if one already exists. Rotating
                this key invalidates every encrypted Connection / Variable
                payload Airflow already wrote — usually you want force=False.
        Returns:
            True if the value is now present in .env (whether unchanged or
            newly written).
        """
        current = self.get_current_env_value('AIRFLOW_FERNET_KEY')
        if not force and current:
            return True
        return self.update_env_key('AIRFLOW_FERNET_KEY', self.generate_airflow_fernet_key())

    def generate_and_update_airflow_secret_key(self, force: bool = False) -> bool:
        """Generate and update AIRFLOW_SECRET_KEY in .env file.

        Args:
            force: Generate new key even if one already exists. Rotating
                invalidates active Web UI sessions and inter-process signed
                payloads — usually you want force=False.
        Returns:
            True if the value is now present in .env.
        """
        current = self.get_current_env_value('AIRFLOW_SECRET_KEY')
        if not force and current:
            return True
        return self.update_env_key('AIRFLOW_SECRET_KEY', self.generate_airflow_secret_key())

    def generate_and_update_airflow_admin_password(self, force: bool = False) -> bool:
        """Generate and update AIRFLOW_ADMIN_PASSWORD in .env file.

        Args:
            force: Generate new password even if one already exists.
                Rotating means the existing admin user's stored hash no
                longer matches; airflow-init re-syncs the user on next
                ./start.sh so this is recoverable, but unexpected for
                users mid-session.
        Returns:
            True if the value is now present in .env.
        """
        current = self.get_current_env_value('AIRFLOW_ADMIN_PASSWORD')
        if not force and current:
            return True
        return self.update_env_key('AIRFLOW_ADMIN_PASSWORD', self.generate_airflow_admin_password())

    def generate_and_update_jenkins_admin_password(self, force: bool = False) -> bool:
        """Generate and update JENKINS_ADMIN_PASSWORD in .env.

        JCasC re-applies the local admin user on startup, so an operator can
        rotate this by editing .env and restarting Jenkins. Preserve existing
        values by default to avoid surprising active sessions.
        """
        current = self.get_current_env_value('JENKINS_ADMIN_PASSWORD')
        if not force and current:
            return True
        return self.update_env_key('JENKINS_ADMIN_PASSWORD', self.generate_jenkins_admin_password())

    def generate_and_update_airflow_db_password(self, force: bool = False) -> bool:
        """Generate and update AIRFLOW_DB_PASSWORD in .env file.

        Args:
            force: Generate new password even if one already exists.
                Rotating requires re-running airflow-init to update the
                Postgres role's password — `./start.sh` handles this.
        Returns:
            True if the value is now present in .env.
        """
        current = self.get_current_env_value('AIRFLOW_DB_PASSWORD')
        if not force and current:
            return True
        return self.update_env_key('AIRFLOW_DB_PASSWORD', self.generate_airflow_db_password())

    def generate_and_update_iceberg_db_password(self, force: bool = False) -> bool:
        """Generate and update ICEBERG_DB_PASSWORD in .env.

        The iceberg-rest init container re-applies the role password on each
        run, so rotations are recoverable after the next ./start.sh. Preserve
        existing values by default to avoid surprising a running catalog.
        """
        current = self.get_current_env_value('ICEBERG_DB_PASSWORD')
        if not force and current:
            return True
        return self.update_env_key('ICEBERG_DB_PASSWORD', self.generate_iceberg_db_password())

    def generate_missing_keys(self, force_regenerate: bool = False) -> Dict[str, bool]:
        """
        Generate any missing encryption keys.

        Args:
            force_regenerate: Force regeneration of existing keys

        Returns:
            dict: Dictionary with key names and success status
        """
        results = {}

        # Generate N8N encryption key
        results['N8N_ENCRYPTION_KEY'] = self.generate_and_update_n8n_key(force=force_regenerate)

        # Generate SearxNG secret
        results['SEARXNG_SECRET'] = self.generate_and_update_searxng_secret(force=force_regenerate)

        # LiteLLM master key — never force-regenerate (would invalidate virtual keys
        # and orphan spend history). Only generate when absent.
        results['LITELLM_MASTER_KEY'] = self.generate_and_update_litellm_master_key(force=False)

        # Hermes API bearer key — same posture as LITELLM_MASTER_KEY: only
        # generate when absent. The LiteLLM model_list's hermes-agent row
        # references it via os.environ/HERMES_API_KEY, so rotating it
        # without restarting the LiteLLM container would break routing.
        results['HERMES_API_KEY'] = self.generate_and_update_hermes_api_key(force=False)

        # LightRAG API bearer key + JWT token secret — only generate when
        # LIGHTRAG_SOURCE != disabled and the keys are absent. Same
        # preservation posture as HERMES_API_KEY.
        if self.get_current_env_value("LIGHTRAG_SOURCE") != "disabled":
            if not self.get_current_env_value("LIGHTRAG_API_KEY"):
                results['LIGHTRAG_API_KEY'] = self.generate_and_update_lightrag_api_key(force=False)
            if not self.get_current_env_value("LIGHTRAG_TOKEN_SECRET"):
                results['LIGHTRAG_TOKEN_SECRET'] = self.generate_and_update_lightrag_token_secret(force=False)

        # Open WebUI JWT/session signing key. Upgrades the shipped
        # ``"secret"`` placeholder to a real 32-byte token; preserves
        # any other user-supplied value so logged-in sessions survive
        # restarts. PyJWT 2.10+ logs InsecureKeyLengthWarning for keys
        # < 32 bytes on HS256, which the open-webui-init scripts trip
        # every launch with the placeholder.
        results['OPEN_WEB_UI_SECRET_KEY'] = self.generate_and_update_webui_secret_key(force=False)

        # MinIO root password — never force-regenerate (would lock out console + break
        # provisioning). Only generate when absent.
        results['MINIO_ROOT_PASSWORD'] = self.generate_and_update_minio_root_password(force=False)

        # Grafana admin password — same posture as LITELLM_MASTER_KEY. Only
        # generate when absent; force-overwriting would sign out every
        # logged-in operator session.
        results['GRAFANA_ADMIN_PASSWORD'] = self.generate_and_update_grafana_admin_password(force=False)

        # MinIO per-consumer service-account credentials — only generate when absent.
        # Rotating these means re-running minio-init, which is a deliberate operator action.
        results.update(self.generate_and_update_minio_consumer_keys(force=False))

        # Airflow secrets — Fernet (Connection encryption), session, admin
        # login password, and the airflow-role Postgres password. All
        # `force=False` because rotating any of them mid-run breaks something
        # (Fernet rotation invalidates every stored Connection password;
        # admin/role rotation locks operators out).
        results['AIRFLOW_FERNET_KEY'] = self.generate_and_update_airflow_fernet_key(force=False)
        results['AIRFLOW_SECRET_KEY'] = self.generate_and_update_airflow_secret_key(force=False)
        results['AIRFLOW_ADMIN_PASSWORD'] = self.generate_and_update_airflow_admin_password(force=False)
        results['JENKINS_ADMIN_PASSWORD'] = self.generate_and_update_jenkins_admin_password(force=False)
        results['AIRFLOW_DB_PASSWORD'] = self.generate_and_update_airflow_db_password(force=False)
        results['ICEBERG_DB_PASSWORD'] = self.generate_and_update_iceberg_db_password(force=False)

        # Infrastructure password placeholders shipped in `.env.example`.
        # Each rotator upgrades the well-known default value only — see
        # PLACEHOLDER_DEFAULTS. Hand-supplied real values stick.
        #
        # The three DB passwords below are baked into their data volume at
        # initdb / first-boot (supabase_admin + app role into
        # `supabase-db-data`; neo4j into `neo4j-graph-db-data`) and can't be
        # changed by rewriting .env afterwards. So on a COLD start — where
        # `perform_cold_start_cleanup` wipes those volumes BEFORE this runs —
        # we MUST force a fresh password: the old baked value is gone, and a
        # stale .env value would re-bake into the fresh volume only by luck
        # (or, if .env drifted, produce `password authentication failed`).
        # Passing `force=force_regenerate` (vs the previous hardcoded
        # `force=False`) couples the password regen to the volume wipe, so a
        # cold start always yields a consistent fresh (password, volume) pair.
        # On a normal (non-cold) start force_regenerate is False, so real
        # values still stick and the existing-volume guard still applies.
        results['SUPABASE_DB_PASSWORD'] = self.generate_and_update_supabase_db_password(force=force_regenerate)
        results['SUPABASE_DB_APP_PASSWORD'] = self.generate_and_update_supabase_db_app_password(force=force_regenerate)
        results['GRAPH_DB_PASSWORD'] = self.generate_and_update_graph_db_password(force=force_regenerate)
        # GRAPH_DB_AUTH is rewritten as a side effect of the password rotator
        # (it embeds the password: "neo4j/<password>"). Track its de-placeholdering
        # explicitly so a partial side-effect write surfaces here rather than only
        # tripping the prod placeholder gate later.
        results['GRAPH_DB_AUTH'] = (
            self.get_current_env_value('GRAPH_DB_AUTH')
            != self.PLACEHOLDER_DEFAULTS.get('GRAPH_DB_AUTH')
        )
        results['REDIS_PASSWORD'] = self.generate_and_update_redis_password(force=False)
        results['DASHBOARD_PASSWORD'] = self.generate_and_update_kong_dashboard_password(force=False)
        results['OPEN_WEB_UI_ADMIN_PASSWORD'] = self.generate_and_update_webui_admin_password(force=False)

        return results
