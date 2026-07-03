package com.atlas.spark;

import org.apache.spark.sql.SparkSession;

public final class LakehouseSmoke {
    private LakehouseSmoke() {
    }

    public static void main(String[] args) {
        String landingPath = args.length > 0 ? args[0] : "s3a://landing/airflow-smoke/input.txt";
        String tableName = args.length > 1 ? args[1] : "lakehouse.bronze.airflow_spark_submit_smoke";

        SparkSession spark = SparkSession.builder()
                .appName("atlas-airflow-lakehouse-smoke")
                .getOrCreate();
        try {
            long inputRows = spark.read().text(landingPath).count();
            spark.sql("CREATE NAMESPACE IF NOT EXISTS lakehouse.bronze");
            spark.sql(
                    "CREATE TABLE IF NOT EXISTS " + tableName
                            + " (id BIGINT, note STRING) USING iceberg"
            );
            spark.sql(
                    "INSERT INTO " + tableName
                            + " VALUES (" + inputRows + ", 'airflow-spark-submit')"
            );
            spark.sql("SELECT * FROM " + tableName).show(false);
        } finally {
            spark.stop();
        }
    }
}
