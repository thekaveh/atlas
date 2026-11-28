from __future__ import annotations

import sys

from pyspark.sql import SparkSession


def main() -> None:
    target = sys.argv[1]
    expected = [(1, "atlas"), (2, "silo")]
    spark = SparkSession.builder.appName("atlas-s3a-roundtrip").getOrCreate()
    try:
        spark.createDataFrame(expected, ["id", "value"]).write.mode(
            "overwrite"
        ).parquet(target)
        rows = sorted(
            (row.id, row.value) for row in spark.read.parquet(target).collect()
        )
        if rows != expected:
            # An explicit check: `assert` is stripped under `python -O`.
            raise SystemExit(f"S3A round-trip mismatch: {rows!r}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
