ARG BASE_IMAGE=apache/spark:4.1.2
FROM ${BASE_IMAGE}

# apache/spark:4.1.2 ships hadoop-client-{api,runtime}-3.4.2 but does
# NOT include hadoop-aws, the AWS SDK v2 bundle, or Apache Iceberg's
# Spark runtime. Without these, any s3a:// access or Iceberg table SQL
# throws ClassNotFoundException at runtime:
#   - spark-history can't read s3a://spark-history/ → empty UI forever
#   - spark-connect drivers can't write spark.eventLog.dir=s3a://… →
#     either silent log loss or hard failure depending on Spark policy
#   - Zeppelin's SPARK_SUBMIT_OPTIONS s3a:// access fails on first cell
#   - Airflow DAGs using Spark Connect to write parquet to s3a:// fail
#
# Hadoop 3.4.x's S3AFileSystem requires:
#   - hadoop-aws-3.4.2.jar (matches the bundled hadoop-client version)
#   - bundle-2.x.x.jar    (AWS SDK v2 shaded; declared as runtime dep
#                          in hadoop-aws's POM but NOT shaded into it)
#
# Drop all required jars into /opt/spark/jars so they're on the
# driver+executor classpath without needing `--packages` at submit time.
#
# Spark's Kafka Structured Streaming connector is handled the same way:
# baking the jars keeps notebooks, Zeppelin, Airflow SparkSubmit jobs, and
# Spark Connect drivers deterministic even when the host is offline.

USER root

# Versions: hadoop-aws MUST match hadoop-client (3.4.2). AWS SDK bundle
# version tracks what hadoop-aws-3.4.2's POM declares — verified at
# https://repo.maven.apache.org/maven2/org/apache/hadoop/hadoop-aws/3.4.2/hadoop-aws-3.4.2.pom
# Iceberg 1.11.0 is the first Iceberg release with a Spark 4.1 /
# Scala 2.13 runtime jar. The separate iceberg-aws-bundle is required
# for org.apache.iceberg.aws.s3.S3FileIO against MinIO.
#
# SHA-512s are checked because both jars land on the Spark driver +
# executor classpath at runtime and execute with whatever privileges the
# spark process has. Maven Central only publishes .sha1 / .md5 sidecars
# (verified via the same URL with the trailing `.sha1`); the SHA-512s
# below were computed locally from the canonical-SHA-1 artifacts:
#   shasum -a 1 hadoop-aws-3.4.2.jar  → 16f9de6da5c7862241d55a13d34843404d0cbb88
#   shasum -a 1 bundle-2.29.52.jar    → b63eb928c2ac13bde07746bcfe7fb38bbdb4b5c5
# (matched against repo.maven.apache.org/.../*.jar.sha1).
#
# Bump procedure: on a version arg change, fetch the new jars, run
# `shasum -a 512 <jar>`, and replace the corresponding ARG default.
ARG HADOOP_AWS_VERSION=3.4.2
ARG AWS_SDK_BUNDLE_VERSION=2.29.52
ARG ICEBERG_VERSION=1.11.0
ARG ICEBERG_SPARK_RUNTIME_ARTIFACT=iceberg-spark-runtime-4.1_2.13
ARG ICEBERG_AWS_BUNDLE_ARTIFACT=iceberg-aws-bundle
ARG SPARK_SQL_KAFKA_VERSION=4.1.2
ARG SPARK_SQL_KAFKA_ARTIFACT=spark-sql-kafka-0-10_2.13
ARG SPARK_KAFKA_CONNECTOR_ARTIFACT=spark-token-provider-kafka-0-10_2.13
ARG KAFKA_CLIENTS_VERSION=3.9.2
ARG COMMONS_POOL2_VERSION=2.12.1
ARG HADOOP_AWS_SHA512=c64c5f39a58b2d6b1f33ff5c14f0575b57e29401cddc599899e8b4499bbe6c76225998f6a89dfb6711779057c0a638708d190d9cd6c9fc280d38045f9fbd9cbf
ARG AWS_SDK_BUNDLE_SHA512=a909eb82364c6272cd4ff67aaff4123e4e5de9205d21c27d1f286e7748780a7b4c2b570c41d184f0600fabe482b1dc3f28d9c60ebbd4b8795666e45ecfb4802a
ARG ICEBERG_SPARK_RUNTIME_SHA512=f4620bb2d20777146a769a8a939a5bed658e1d5708b6a5155e1f6e831c0bfd29bd1f82618dc59bc0f30399ffcb03add3e66656286692dc6c2be94e4e1f4e7479
ARG ICEBERG_AWS_BUNDLE_SHA512=bb50f7dc5f36a001efecf15e4d03eff955466f06557261fbacdd7fe0d88113c82e0b9b2af96cd554cef4a1d4c2348fc950270bc3f51cf6bf1e4526f46ce336a0
ARG SPARK_SQL_KAFKA_SHA512=57212eeb69ec417a2ab84dcf9de882fea95eb5554bd9adf10698fb5621fe0127283fd3bad4bb19897598673d06fa087de9fdf0f9614ec4004248e56e8a80ba94
ARG SPARK_KAFKA_CONNECTOR_SHA512=68a83987ad79923effa7b0e1657a8eb987857714502fffc59326a2784312b15bf943fbc88b257215869f68853be79322687c443806f9f78e15a6cae2ceffbe5c
ARG KAFKA_CLIENTS_SHA512=dd824c9a31f38a5a6d2b2c7ac03e9f31d8c5a7f1d8a6baefbe7d55552f4ab961ec831f040bb87a3c52dab6d464ee0954c31d256c1ea323140139465697806369
ARG COMMONS_POOL2_SHA512=186dabefa07a38cc106e5aca3fcec6d2d8c79fc2787d448632c24a61cad3574c53809895f85181ba097012b60a86116eec22ae3ab4c6e47ac7dddac00a0b71a7

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL -o /opt/spark/jars/hadoop-aws-${HADOOP_AWS_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/apache/hadoop/hadoop-aws/${HADOOP_AWS_VERSION}/hadoop-aws-${HADOOP_AWS_VERSION}.jar"; \
    echo "${HADOOP_AWS_SHA512}  /opt/spark/jars/hadoop-aws-${HADOOP_AWS_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o /opt/spark/jars/bundle-${AWS_SDK_BUNDLE_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/software/amazon/awssdk/bundle/${AWS_SDK_BUNDLE_VERSION}/bundle-${AWS_SDK_BUNDLE_VERSION}.jar"; \
    echo "${AWS_SDK_BUNDLE_SHA512}  /opt/spark/jars/bundle-${AWS_SDK_BUNDLE_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o /opt/spark/jars/${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/apache/iceberg/${ICEBERG_SPARK_RUNTIME_ARTIFACT}/${ICEBERG_VERSION}/${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar"; \
    echo "${ICEBERG_SPARK_RUNTIME_SHA512}  /opt/spark/jars/${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o /opt/spark/jars/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/apache/iceberg/${ICEBERG_AWS_BUNDLE_ARTIFACT}/${ICEBERG_VERSION}/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar"; \
    echo "${ICEBERG_AWS_BUNDLE_SHA512}  /opt/spark/jars/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o /opt/spark/jars/spark-sql-kafka-0-10_2.13-${SPARK_SQL_KAFKA_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/apache/spark/${SPARK_SQL_KAFKA_ARTIFACT}/${SPARK_SQL_KAFKA_VERSION}/${SPARK_SQL_KAFKA_ARTIFACT}-${SPARK_SQL_KAFKA_VERSION}.jar"; \
    echo "${SPARK_SQL_KAFKA_SHA512}  /opt/spark/jars/spark-sql-kafka-0-10_2.13-${SPARK_SQL_KAFKA_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o /opt/spark/jars/spark-token-provider-kafka-0-10_2.13-${SPARK_SQL_KAFKA_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/apache/spark/${SPARK_KAFKA_CONNECTOR_ARTIFACT}/${SPARK_SQL_KAFKA_VERSION}/${SPARK_KAFKA_CONNECTOR_ARTIFACT}-${SPARK_SQL_KAFKA_VERSION}.jar"; \
    echo "${SPARK_KAFKA_CONNECTOR_SHA512}  /opt/spark/jars/spark-token-provider-kafka-0-10_2.13-${SPARK_SQL_KAFKA_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o /opt/spark/jars/kafka-clients-${KAFKA_CLIENTS_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/apache/kafka/kafka-clients/${KAFKA_CLIENTS_VERSION}/kafka-clients-${KAFKA_CLIENTS_VERSION}.jar"; \
    echo "${KAFKA_CLIENTS_SHA512}  /opt/spark/jars/kafka-clients-${KAFKA_CLIENTS_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o /opt/spark/jars/commons-pool2-${COMMONS_POOL2_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/apache/commons/commons-pool2/${COMMONS_POOL2_VERSION}/commons-pool2-${COMMONS_POOL2_VERSION}.jar"; \
    echo "${COMMONS_POOL2_SHA512}  /opt/spark/jars/commons-pool2-${COMMONS_POOL2_VERSION}.jar" | sha512sum -c -

USER spark
