#!/usr/bin/env python3
"""Register Open WebUI functions (filters, pipes) from filesystem via the API on startup."""

import os
import re
import sys
import time
import glob
import jwt
import requests
import psycopg2

# Force line-buffered stdout so the init container's progress prints
# reach `docker logs` immediately even when stdout is pipe-attached
# (block-buffered by default). Mirrors the per-print `flush=True` the
# litellm-init / lightrag-init scripts use, applied once at the
# module level instead of every print site.
sys.stdout.reconfigure(line_buffering=True)


WEBUI_URL = os.environ["WEBUI_URL"]
WEBUI_SECRET_KEY = os.environ["WEBUI_SECRET_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]
FUNCTIONS_DIR = os.environ.get("FUNCTIONS_DIR", "/functions")

MAX_RETRIES = 60
RETRY_INTERVAL = 5


def wait_for_webui():
    """Wait until Open WebUI API is reachable."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(f"{WEBUI_URL}/api/v1/functions/", timeout=5)
            if resp.status_code in (200, 401, 403):
                return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            pass
        time.sleep(RETRY_INTERVAL)
    print("open-webui-init: ERROR - Open WebUI not available for function registration")
    return False


def get_admin_user_id():
    """Query the database for the admin user ID."""
    conn = None
    cursor = None
    try:
        # connect_timeout caps the TCP-handshake stage.
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM public.\"user\" WHERE role = 'admin' LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            return row[0]
    except psycopg2.Error as e:
        print(f"open-webui-init: Function registration - DB query failed: {e}")
    finally:
        # Close on the error path too so a restart loop doesn't leak
        # one connection per attempt.
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
    return None


def generate_token(admin_id):
    """Generate a JWT for the admin user."""
    payload = {"id": admin_id, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, WEBUI_SECRET_KEY, algorithm="HS256")


def parse_function_metadata(content):
    """Extract title, description, and type from the function's docstring header."""
    metadata = {}
    match = re.search(r'^"""(.*?)"""', content, re.DOTALL)
    if match:
        docstring = match.group(1)
        for line in docstring.strip().splitlines():
            line = line.strip()
            if line.startswith("title:"):
                metadata["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                metadata["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("type:"):
                metadata["type"] = line.split(":", 1)[1].strip()
    return metadata


def function_exists(function_id, headers):
    """Check if a function already exists in Open WebUI."""
    resp = requests.get(
        f"{WEBUI_URL}/api/v1/functions/id/{function_id}", headers=headers, timeout=10
    )
    return resp.status_code == 200


def create_function(function_id, name, content, description, func_type, headers):
    """Create a new function via the API."""
    payload = {
        "id": function_id,
        "name": name,
        "content": content,
        "type": func_type,
        "meta": {"description": description},
    }
    resp = requests.post(
        f"{WEBUI_URL}/api/v1/functions/create",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def update_function(function_id, name, content, description, func_type, headers):
    """Update an existing function via the API."""
    payload = {
        "id": function_id,
        "name": name,
        "content": content,
        "type": func_type,
        "meta": {"description": description},
    }
    resp = requests.post(
        f"{WEBUI_URL}/api/v1/functions/id/{function_id}/update",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def register_functions(token):
    """Read function files and register/update them via the API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    function_files = sorted(glob.glob(os.path.join(FUNCTIONS_DIR, "*.py")))
    if not function_files:
        print(f"open-webui-init: No .py files found in {FUNCTIONS_DIR}")
        return

    print(f"open-webui-init: Found {len(function_files)} function file(s)")

    failures = []
    for filepath in function_files:
        filename = os.path.basename(filepath)
        function_id = re.sub(r"[^a-zA-Z0-9_]", "_", os.path.splitext(filename)[0])

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        metadata = parse_function_metadata(content)
        name = metadata.get("title", function_id.replace("_", " ").title())
        description = metadata.get("description", "")
        func_type = metadata.get("type", "filter")

        try:
            if function_exists(function_id, headers):
                update_function(function_id, name, content, description, func_type, headers)
                print(f"open-webui-init: Updated function: {name} ({function_id})")
            else:
                create_function(function_id, name, content, description, func_type, headers)
                print(f"open-webui-init: Created function: {name} ({function_id})")
        except Exception as e:
            print(f"open-webui-init: ERROR registering function {function_id}: {e}")
            failures.append(function_id)

    if failures:
        raise RuntimeError(
            f"Failed to register {len(failures)} Open WebUI function(s): "
            + ", ".join(failures)
        )


def main():
    if not os.path.isdir(FUNCTIONS_DIR):
        print(f"open-webui-init: Functions directory {FUNCTIONS_DIR} not found, skipping")
        return

    function_files = glob.glob(os.path.join(FUNCTIONS_DIR, "*.py"))
    if not function_files:
        print("open-webui-init: No function files found, skipping function registration")
        return

    if not wait_for_webui():
        sys.exit(1)

    admin_id = get_admin_user_id()
    if not admin_id:
        print("open-webui-init: ERROR - No admin user found. Cannot register functions.")
        sys.exit(1)

    token = generate_token(admin_id)

    # Verify token works
    resp = requests.get(
        f"{WEBUI_URL}/api/v1/functions/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"open-webui-init: ERROR - Auth failed for functions (HTTP {resp.status_code})")
        sys.exit(1)

    register_functions(token)
    print("open-webui-init: Function registration complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print("open-webui-init: Function registration failed with exception:")
        traceback.print_exc()
        sys.exit(1)
