"""Helpers shared by the Open WebUI registration init scripts.

register-tools.py and register-functions.py run from /app/scripts in the
open-webui-init container (`python3 /app/scripts/<script>.py`). Python puts the
script's own directory first on sys.path, so `import register_helpers` resolves
without any path setup. The Dockerfile's `COPY scripts/ /app/scripts/` ships this
file alongside the scripts.

Nothing here reads the environment or opens a connection at import time. Callers
pass in the endpoint, secret or DSN. Retry loops and log lines stay in each
script, because the two scripts deliberately differ there.
"""

import re
import time
from typing import NamedTuple

import jwt
import psycopg2

import requests


def api_reachable(base_url, path):
    """Return True when the Open WebUI API answers on ``path``.

    401 and 403 count as up: the service is running, the request is just not
    authenticated. Connection errors and timeouts return False. Any other
    exception propagates to the caller.
    """
    try:
        resp = requests.get(f"{base_url}{path}", timeout=5)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return False
    return resp.status_code in (200, 401, 403)


def fetch_admin_row(database_url):
    """Return the first ``role = 'admin'`` row of ``public."user"``, or None.

    Raises ``psycopg2.Error`` on connect or query failure, so the caller decides
    whether to retry. The cursor and connection are closed on every path, so a
    retry loop does not leak one connection per attempt.
    """
    conn = None
    cursor = None
    try:
        # connect_timeout caps the TCP-handshake stage so a stalled DB host
        # does not push each attempt to the ~75s OS-level connect timeout.
        conn = psycopg2.connect(database_url, connect_timeout=5)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM public.\"user\" WHERE role = 'admin' LIMIT 1"
        )
        return cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def make_token(admin_id, secret):
    """Generate a one-hour HS256 JWT for the admin user."""
    payload = {"id": admin_id, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, secret, algorithm="HS256")


def parse_docstring_header(content, fields):
    """Return ``{field: value}`` for ``field:`` lines in the leading docstring.

    ``fields`` lists the keys this artifact type reads. Other header lines are
    ignored.
    """
    metadata = {}
    # Match the triple-quoted docstring at the top of the file
    match = re.search(r'^"""(.*?)"""', content, re.DOTALL)
    if match:
        docstring = match.group(1)
        for line in docstring.strip().splitlines():
            line = line.strip()
            for field in fields:
                if line.startswith(f"{field}:"):
                    metadata[field] = line.split(":", 1)[1].strip()
                    break
    return metadata


def artifact_exists(base_url, resource, artifact_id, headers):
    """Return True when GET ``/api/v1/<resource>/id/<artifact_id>`` returns 200."""
    resp = requests.get(
        f"{base_url}/api/v1/{resource}/id/{artifact_id}", headers=headers, timeout=10
    )
    return resp.status_code == 200


class Artifact(NamedTuple):
    """The four fields every Open WebUI tool or function artifact shares."""

    id: str
    name: str
    content: str
    description: str


def _artifact_payload(artifact, extra):
    return {
        "id": artifact.id,
        "name": artifact.name,
        "content": artifact.content,
        **extra,
        "meta": {"description": artifact.description},
    }


def create_artifact(base_url, resource, artifact, headers, **extra):
    """POST a new artifact to ``/api/v1/<resource>/create``.

    ``extra`` adds payload fields that only some artifact types send, such as
    ``type`` for functions.
    """
    payload = _artifact_payload(artifact, extra)
    resp = requests.post(
        f"{base_url}/api/v1/{resource}/create",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def update_artifact(base_url, resource, artifact, headers, **extra):
    """POST changes to ``/api/v1/<resource>/id/<artifact_id>/update``."""
    payload = _artifact_payload(artifact, extra)
    resp = requests.post(
        f"{base_url}/api/v1/{resource}/id/{artifact.id}/update",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
