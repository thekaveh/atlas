"""Import one Airflow Connection from environment variables.

add_conn in init-airflow.sh exports CONN_* and calls this helper. Passwords and
extras therefore never reach argv (#1381). The import file is created mode 0600
and removed after use.
"""

import json
import os
import subprocess
import tempfile

FIELDS = {
    "conn_type": "CONN_TYPE",
    "host": "CONN_HOST",
    "port": "CONN_PORT",
    "login": "CONN_LOGIN",
    "password": "CONN_PASSWORD",
    "schema": "CONN_SCHEMA",
    "extra": "CONN_EXTRA",
}

conn_id = os.environ["CONN_ID"]
spec = {}
for key, env_name in FIELDS.items():
    value = os.environ.get(env_name, "")
    if value:
        spec[key] = int(value) if key == "port" else value

fd, path = tempfile.mkstemp(suffix=".json")
try:
    with os.fdopen(fd, "w") as handle:
        json.dump({conn_id: spec}, handle)
    subprocess.run(["airflow", "connections", "import", "--overwrite", path], check=True)
finally:
    os.remove(path)
