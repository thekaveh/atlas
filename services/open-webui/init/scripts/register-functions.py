#!/usr/bin/env python3
"""Register Open WebUI functions (filters, pipes) from filesystem via the API on startup."""

import os
import re
import sys
import time
import glob
import requests
import psycopg2

from register_helpers import (
    Artifact,
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
FUNCTIONS_DIR = os.environ.get("FUNCTIONS_DIR", "/functions")

MAX_RETRIES = 60
RETRY_INTERVAL = 5


def wait_for_webui():
    """Wait until Open WebUI API is reachable."""
    for attempt in range(1, MAX_RETRIES + 1):
        if api_reachable(WEBUI_URL, "/api/v1/functions/"):
            return True
        time.sleep(RETRY_INTERVAL)
    print("open-webui-init: ERROR - Open WebUI not available for function registration")
    return False


def get_admin_user_id():
    """Query the database for the admin user ID."""
    try:
        row = fetch_admin_row(DATABASE_URL)
        if row:
            return row[0]
    except psycopg2.Error as e:
        print(f"open-webui-init: Function registration - DB query failed: {e}")
    return None


def parse_function_metadata(content):
    """Extract title, description, and type from the function's docstring header."""
    return parse_docstring_header(content, ("title", "description", "type"))


def function_exists(function_id, headers):
    """Check if a function already exists in Open WebUI."""
    return artifact_exists(WEBUI_URL, "functions", function_id, headers)


def create_function(function_id, name, content, description, func_type, headers):
    """Create a new function via the API."""
    return create_artifact(
        WEBUI_URL, "functions", Artifact(function_id, name, content, description), headers,
        type=func_type,
    )


def update_function(function_id, name, content, description, func_type, headers):
    """Update an existing function via the API."""
    return update_artifact(
        WEBUI_URL, "functions", Artifact(function_id, name, content, description), headers,
        type=func_type,
    )


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

    token = make_token(admin_id, WEBUI_SECRET_KEY)

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
