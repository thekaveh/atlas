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
) -> tuple[dict[str, object], bool]:
    merged = copy.deepcopy(existing)
    changed = False

    for name in ATLAS_REMOVED_PROPERTIES:
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


def needs_restart(before: dict[str, object], after: dict[str, object]) -> bool:
    return before != after


def find_spark_setting(settings: list[dict[str, object]]) -> dict[str, object]:
    for setting in settings:
        if setting.get("name") == "spark" and setting.get("group") == "spark":
            return setting
    raise RuntimeError("could not find Zeppelin spark interpreter setting")


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


def main() -> int:
    base_url = _env(os.environ, "ZEPPELIN_URL", "http://zeppelin:8080")
    wait_for_url(f"{base_url.rstrip('/')}/api/interpreter/setting")

    if _env(os.environ, "ICEBERG_REST_SOURCE", "disabled") != "disabled":
        wait_for_url(f"{_env(os.environ, 'ICEBERG_REST_URI', 'http://iceberg-rest:8181')}/v1/config")

    seed_interpreter(base_url, dict(os.environ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
