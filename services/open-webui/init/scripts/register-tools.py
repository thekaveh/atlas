#!/usr/bin/env python3
"""Register Open WebUI tools from filesystem via the API on startup."""

import os
import re
import sys
import time
import glob
import requests
import psycopg2

from register_helpers import (
    api_reachable,
    artifact_exists,
    create_artifact,
    fetch_admin_row,
    make_token,
    parse_docstring_header,
    update_artifact,
)

# Force line-buffered stdout so the init container's progress prints
# reach `docker logs` immediately even when stdout is pipe-attached
# (block-buffered by default). Mirrors the per-print `flush=True` the
# litellm-init / lightrag-init scripts use, applied once at the
# module level instead of every print site.
sys.stdout.reconfigure(line_buffering=True)


WEBUI_URL = os.environ["WEBUI_URL"]
WEBUI_SECRET_KEY = os.environ["WEBUI_SECRET_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]
TOOLS_DIR = os.environ.get("TOOLS_DIR", "/tools")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@localhost")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Admin")

MAX_RETRIES = 60
RETRY_INTERVAL = 5
UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def wait_for_webui():
    """Wait until Open WebUI API is reachable."""
    print("open-webui-init: Waiting for Open WebUI to be ready...")
    for attempt in range(1, MAX_RETRIES + 1):
        # 401 (not authenticated) also means the service is up
        if api_reachable(WEBUI_URL, "/api/v1/tools/"):
            print("open-webui-init: Open WebUI is ready")
            return True
        print(f"open-webui-init: Waiting for Open WebUI (attempt {attempt}/{MAX_RETRIES})...")
        time.sleep(RETRY_INTERVAL)
    print("open-webui-init: ERROR - Open WebUI not available after max retries")
    return False


def create_admin_user():
    """Create admin user via the signup API (first user gets admin role)."""
    print(f"open-webui-init: Creating admin user ({ADMIN_EMAIL})...")
    try:
        resp = requests.post(
            f"{WEBUI_URL}/api/v1/auths/signup",
            json={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "name": ADMIN_NAME,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"open-webui-init: Admin user created: {data.get('email')}")
            return data.get("id")
        else:
            detail = resp.json().get("detail", resp.text) if resp.text else resp.status_code
            print(f"open-webui-init: Signup failed: {detail}")
            return None
    except Exception as e:
        print(f"open-webui-init: Signup request failed: {e}")
        return None


def get_admin_user_id():
    """Query the database for the admin user ID, creating one if needed."""
    print("open-webui-init: Looking for admin user in database...")
    for attempt in range(1, MAX_RETRIES + 1):
        # fetch_admin_row sets connect_timeout=5 and closes the connection on
        # every path, including the error path (restart loops would otherwise
        # leak one connection per attempt).
        try:
            row = fetch_admin_row(DATABASE_URL)
            if row:
                admin_id = row[0]
                print(f"open-webui-init: Found admin user: {admin_id}")
                return admin_id
        except psycopg2.Error as e:
            print(f"open-webui-init: Database query failed: {e}")
            time.sleep(RETRY_INTERVAL)
            continue

        # No admin exists — try to create one via signup API
        admin_id = create_admin_user()
        if admin_id:
            return admin_id

        print(
            f"open-webui-init: Admin user not found yet (attempt {attempt}/{MAX_RETRIES})..."
        )
        time.sleep(RETRY_INTERVAL)
    return None


def backend_user_sync_sql():
    return f"""
    CREATE OR REPLACE FUNCTION public.handle_open_webui_user_sync()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = ''
    AS $sync$
    DECLARE
        candidate_id text := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    BEGIN
        IF candidate_id !~* '{UUID_PATTERN}' THEN
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END IF;
        IF TG_OP = 'DELETE' THEN
            DELETE FROM public.users
            WHERE id = candidate_id::uuid
              AND NOT EXISTS (
                  SELECT 1 FROM auth.users WHERE id = candidate_id::uuid
              );
            RETURN OLD;
        END IF;
        -- A GoTrue identity owns its row; skip before INSERT, because RLS
        -- checks the insert before ON CONFLICT ... WHERE could skip it.
        IF EXISTS (SELECT 1 FROM auth.users WHERE id = candidate_id::uuid) THEN
            RETURN NEW;
        END IF;
        INSERT INTO public.users (id, name)
        VALUES (
            candidate_id::uuid,
            COALESCE(NULLIF(BTRIM(NEW.name), ''), 'Open WebUI user')
        )
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
        WHERE NOT EXISTS (
            SELECT 1 FROM auth.users WHERE id = candidate_id::uuid
        );
        RETURN NEW;
    END;
    $sync$;

    REVOKE ALL ON FUNCTION public.handle_open_webui_user_sync()
        FROM PUBLIC, anon, authenticated, service_role;
    DROP TRIGGER IF EXISTS on_open_webui_user_sync ON public."user";
    CREATE TRIGGER on_open_webui_user_sync
        AFTER INSERT OR DELETE OR UPDATE OF name ON public."user"
        FOR EACH ROW EXECUTE FUNCTION public.handle_open_webui_user_sync();

    INSERT INTO public.users (id, name)
    SELECT
        id::uuid,
        COALESCE(NULLIF(BTRIM(name), ''), 'Open WebUI user')
    FROM public."user"
    WHERE id ~* '{UUID_PATTERN}'
      -- text comparison: Postgres may evaluate this before the UUID regex,
      -- and casting a non-UUID id would abort the whole backfill.
      AND NOT EXISTS (SELECT 1 FROM auth.users a WHERE a.id::text = lower("user".id))
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
    WHERE NOT EXISTS (
        SELECT 1 FROM auth.users WHERE id = public.users.id
    );
    """


def install_backend_user_sync():
    """Keep Open WebUI identities valid for Backend memory foreign keys."""
    print("open-webui-init: Synchronizing Open WebUI users with Backend owners...")
    with psycopg2.connect(DATABASE_URL, connect_timeout=5) as conn:
        with conn.cursor() as cursor:
            cursor.execute(backend_user_sync_sql())
    print("open-webui-init: Backend owner synchronization ready")


def parse_tool_metadata(content):
    """Extract title and description from the tool's docstring header."""
    return parse_docstring_header(content, ("title", "description"))


def tool_exists(tool_id, headers):
    """Check if a tool already exists in Open WebUI."""
    return artifact_exists(WEBUI_URL, "tools", tool_id, headers)


def create_tool(tool_id, name, content, description, headers):
    """Create a new tool via the API."""
    return create_artifact(WEBUI_URL, "tools", tool_id, name, content, description, headers)


def update_tool(tool_id, name, content, description, headers):
    """Update an existing tool via the API."""
    return update_artifact(WEBUI_URL, "tools", tool_id, name, content, description, headers)


def register_tools(token):
    """Read tool files and register/update them via the API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    tool_files = sorted(glob.glob(os.path.join(TOOLS_DIR, "*.py")))
    if not tool_files:
        print(f"open-webui-init: No .py files found in {TOOLS_DIR}")
        return

    print(f"open-webui-init: Found {len(tool_files)} tool file(s)")

    failures = []
    for filepath in tool_files:
        filename = os.path.basename(filepath)
        # Open WebUI requires alphanumeric + underscores only
        tool_id = re.sub(r"[^a-zA-Z0-9_]", "_", os.path.splitext(filename)[0])

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        metadata = parse_tool_metadata(content)
        name = metadata.get("title", tool_id.replace("_", " ").title())
        description = metadata.get("description", "")

        try:
            if tool_exists(tool_id, headers):
                update_tool(tool_id, name, content, description, headers)
                print(f"open-webui-init: Updated tool: {name} ({tool_id})")
            else:
                create_tool(tool_id, name, content, description, headers)
                print(f"open-webui-init: Created tool: {name} ({tool_id})")
        except Exception as e:
            print(f"open-webui-init: ERROR registering {tool_id}: {e}")
            failures.append(tool_id)

    if failures:
        raise RuntimeError(
            f"Failed to register {len(failures)} Open WebUI tool(s): "
            + ", ".join(failures)
        )


def main():
    if not wait_for_webui():
        sys.exit(1)

    install_backend_user_sync()

    admin_id = get_admin_user_id()
    if not admin_id:
        print("open-webui-init: ERROR - No admin user found. Cannot register tools.")
        sys.exit(1)

    token = make_token(admin_id, WEBUI_SECRET_KEY)

    # Verify token works
    resp = requests.get(
        f"{WEBUI_URL}/api/v1/tools/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"open-webui-init: ERROR - Auth failed (HTTP {resp.status_code})")
        sys.exit(1)

    register_tools(token)
    print("open-webui-init: Tool registration complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print("open-webui-init: Tool registration failed with exception:")
        traceback.print_exc()
        sys.exit(1)
