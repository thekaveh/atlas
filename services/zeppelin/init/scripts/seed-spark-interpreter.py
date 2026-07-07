#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request


sys.stdout.reconfigure(line_buffering=True)

ATLAS_REMOVED_PROPERTIES = ("spark.remote", "SPARK_REMOTE")
TRINO_JDBC_SETTING_NAME = "trino"
TRINO_JDBC_GROUP = "jdbc"
TRINO_JDBC_DEFAULT_DRIVER = "io.trino.jdbc.TrinoDriver"
TRINO_JDBC_DEFAULT_URL = "jdbc:trino://trino:8080/lakehouse"
TRINO_JDBC_DEFAULT_USER = "atlas"
TRINO_JDBC_DEFAULT_DEPENDENCY = "io.trino:trino-jdbc:482"


def _env(env: dict[str, str], name: str, default: str = "") -> str:
    value = env.get(name, default)
    return default if value is None or value == "" else value


def build_atlas_properties(env: dict[str, str]) -> dict[str, str]:
    minio_endpoint = _env(env, "MINIO_ENDPOINT", "http://minio:9000")
    minio_region = _env(env, "MINIO_REGION", "us-east-1")
    lakehouse_bucket = _env(env, "MINIO_BUCKET_ICEBERG_LAKEHOUSE", "lakehouse")
    iceberg_rest_uri = _env(env, "ICEBERG_REST_URI", "http://iceberg-rest:8181")

    return {
        "JAVA_HOME": _env(env, "JAVA_HOME", "/opt/java/openjdk"),
        "SPARK_HOME": _env(env, "SPARK_HOME", "/opt/spark"),
        "spark.master": _env(env, "SPARK_MASTER", "spark://spark-master:7077"),
        "zeppelin.spark.enableSupportedVersionCheck": "false",
        "spark.submit.deployMode": "client",
        "spark.driver.bindAddress": "0.0.0.0",
        "spark.driver.host": "zeppelin",
        "spark.hadoop.fs.s3a.endpoint": minio_endpoint,
        "spark.hadoop.fs.s3a.access.key": _env(env, "MINIO_ROOT_USER"),
        "spark.hadoop.fs.s3a.secret.key": _env(env, "MINIO_ROOT_PASSWORD"),
        "spark.hadoop.fs.s3a.path.style.access": "true",
        "spark.hadoop.fs.s3a.endpoint.region": minio_region,
        "spark.hadoop.fs.s3a.connection.ssl.enabled": "false",
        "spark.eventLog.enabled": "true",
        "spark.eventLog.dir": "s3a://spark-history/",
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.lakehouse": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.lakehouse.type": "rest",
        "spark.sql.catalog.lakehouse.uri": iceberg_rest_uri,
        "spark.sql.catalog.lakehouse.warehouse": f"s3a://{lakehouse_bucket}/",
        "spark.sql.catalog.lakehouse.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
        "spark.sql.catalog.lakehouse.s3.endpoint": minio_endpoint,
        "spark.sql.catalog.lakehouse.s3.path-style-access": "true",
        "spark.sql.catalog.lakehouse.s3.access-key-id": _env(
            env,
            "MINIO_ICEBERG_ACCESS_KEY",
        ),
        "spark.sql.catalog.lakehouse.s3.secret-access-key": _env(
            env,
            "MINIO_ICEBERG_SECRET_KEY",
        ),
        "spark.sql.catalog.lakehouse.client.region": minio_region,
    }


def property_entry(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value, "type": "string"}


def merge_properties(
    existing: dict[str, object],
    desired: dict[str, str],
    removed: tuple[str, ...] = ATLAS_REMOVED_PROPERTIES,
) -> tuple[dict[str, object], bool]:
    merged = copy.deepcopy(existing)
    changed = False

    for name in removed:
        if name in merged:
            del merged[name]
            changed = True

    for name, value in desired.items():
        entry = property_entry(name, value)
        current = merged.get(name)
        if not isinstance(current, dict) or current.get("value") != value:
            merged[name] = entry
            changed = True

    return merged, changed


def merge_dependencies(
    existing: list[object],
    desired: dict[str, object],
) -> tuple[list[object], bool]:
    merged = copy.deepcopy(existing)
    desired_coord = desired.get("groupArtifactVersion")

    for dep in merged:
        if isinstance(dep, dict) and dep.get("groupArtifactVersion") == desired_coord:
            if dep == desired:
                return merged, False
            dep.update(desired)
            return merged, True

    merged.append(desired)
    return merged, True


def needs_restart(before: dict[str, object], after: dict[str, object]) -> bool:
    """Whether the merged interpreter config differs from the existing one.

    The runtime restart decision is made inline in seed_interpreter /
    seed_trino_interpreter (a PUT to /restart/{id} whenever the config
    changed), but this predicate documents the same comparison and is
    exercised by test_zeppelin_lakehouse_seed — keep it (do not re-delete as
    dead code; the test asserts the restart contract through it).
    """
    return before != after


def find_spark_setting(settings: list[dict[str, object]]) -> dict[str, object]:
    for setting in settings:
        if setting.get("name") == "spark" and setting.get("group") == "spark":
            return setting
    raise RuntimeError("could not find Zeppelin spark interpreter setting")


def find_trino_setting(settings: list[dict[str, object]]) -> dict[str, object] | None:
    for setting in settings:
        if setting.get("name") == TRINO_JDBC_SETTING_NAME and setting.get("group") == TRINO_JDBC_GROUP:
            return setting
    return None


def should_seed_trino(env: dict[str, str]) -> bool:
    return _env(env, "TRINO_SOURCE", "disabled") != "disabled"


def build_trino_jdbc_properties(env: dict[str, str]) -> dict[str, str]:
    properties = {
        "default.driver": _env(env, "TRINO_JDBC_DRIVER", TRINO_JDBC_DEFAULT_DRIVER),
        "default.url": _env(env, "TRINO_JDBC_URL", TRINO_JDBC_DEFAULT_URL),
        "default.user": _env(env, "TRINO_JDBC_USER", TRINO_JDBC_DEFAULT_USER),
    }
    password = _env(env, "TRINO_JDBC_PASSWORD")
    if password:
        properties["default.password"] = password
    return properties


def build_trino_jdbc_dependency(env: dict[str, str]) -> dict[str, object]:
    return {
        "groupArtifactVersion": _env(
            env,
            "TRINO_JDBC_DEPENDENCY",
            TRINO_JDBC_DEFAULT_DEPENDENCY,
        ),
        "local": False,
    }


def build_trino_jdbc_setting(
    existing: dict[str, object] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    env = env or {}
    setting = copy.deepcopy(existing) if existing else {}
    setting["name"] = TRINO_JDBC_SETTING_NAME
    setting["group"] = TRINO_JDBC_GROUP
    setting.setdefault(
        "interpreterGroup",
        [
            {
                "class": "org.apache.zeppelin.jdbc.JDBCInterpreter",
                "name": TRINO_JDBC_GROUP,
            }
        ],
    )
    setting.setdefault(
        "option",
        {
            "remote": True,
            "perNote": "shared",
            "perUser": "shared",
            "isExistingProcess": False,
            "setPermission": False,
        },
    )

    properties = setting.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    merged_properties, _ = merge_properties(
        properties,
        build_trino_jdbc_properties(env),
        removed=(),
    )
    setting["properties"] = merged_properties

    dependencies = setting.get("dependencies") or []
    if not isinstance(dependencies, list):
        dependencies = []
    merged_dependencies, _ = merge_dependencies(
        dependencies,
        build_trino_jdbc_dependency(env),
    )
    setting["dependencies"] = merged_dependencies

    return setting


def request_json(
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    timeout: int = 15,
) -> dict[str, object]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    if parsed.get("status") != "OK":
        raise RuntimeError(f"Zeppelin API returned non-OK status for {url}: {parsed}")
    return parsed


def wait_for_url(url: str, timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if 200 <= resp.status < 500:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(2)

    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


def seed_interpreter(base_url: str, env: dict[str, str]) -> bool:
    settings_url = f"{base_url.rstrip('/')}/api/interpreter/setting"
    settings = request_json("GET", settings_url)["body"]
    if not isinstance(settings, list):
        raise RuntimeError("unexpected Zeppelin interpreter settings payload")

    setting = find_spark_setting(settings)
    setting_id = setting["id"]
    before = setting.get("properties") or {}
    if not isinstance(before, dict):
        before = {}

    after, changed = merge_properties(before, build_atlas_properties(env))
    if not changed:
        print("zeppelin-init: spark interpreter already configured")
        return False

    updated = copy.deepcopy(setting)
    updated["properties"] = after
    request_json("PUT", f"{settings_url}/{setting_id}", updated)
    request_json("PUT", f"{settings_url}/restart/{setting_id}", {})
    print("zeppelin-init: spark interpreter configured and restarted")
    return True


def seed_trino_interpreter(base_url: str, env: dict[str, str]) -> bool:
    if not should_seed_trino(env):
        print("zeppelin-init: trino source disabled; skipping JDBC interpreter")
        return False

    settings_url = f"{base_url.rstrip('/')}/api/interpreter/setting"
    settings = request_json("GET", settings_url)["body"]
    if not isinstance(settings, list):
        raise RuntimeError("unexpected Zeppelin interpreter settings payload")

    setting = find_trino_setting(settings)
    if setting is None:
        created = build_trino_jdbc_setting(env=env)
        request_json("POST", settings_url, created)
        print("zeppelin-init: trino JDBC interpreter created")
        return True

    setting_id = setting["id"]
    updated = build_trino_jdbc_setting(setting, env)
    if updated == setting:
        print("zeppelin-init: trino JDBC interpreter already configured")
        return False

    request_json("PUT", f"{settings_url}/{setting_id}", updated)
    request_json("PUT", f"{settings_url}/restart/{setting_id}", {})
    print("zeppelin-init: trino JDBC interpreter configured and restarted")
    return True


def main() -> int:
    base_url = _env(os.environ, "ZEPPELIN_URL", "http://zeppelin:8080")
    wait_for_url(f"{base_url.rstrip('/')}/api/interpreter/setting")

    if _env(os.environ, "ICEBERG_REST_SOURCE", "disabled") != "disabled":
        wait_for_url(f"{_env(os.environ, 'ICEBERG_REST_URI', 'http://iceberg-rest:8181')}/v1/config")
    if should_seed_trino(dict(os.environ)):
        wait_for_url(f"{_env(os.environ, 'TRINO_ENDPOINT', 'http://trino:8080')}/v1/info")

    env = dict(os.environ)
    seed_interpreter(base_url, env)
    seed_trino_interpreter(base_url, env)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback
        print(f"zeppelin-init: ERROR: {exc}")
        traceback.print_exc()
        sys.exit(1)
