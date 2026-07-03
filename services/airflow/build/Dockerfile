ARG BASE_IMAGE=apache/airflow:3.2.2
FROM ${BASE_IMAGE}

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl openjdk-17-jdk-headless; \
    rm -rf /var/lib/apt/lists/*; \
    java_home="$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")"; \
    mkdir -p /opt/java; \
    ln -sfn "${java_home}" /opt/java/openjdk

ENV JAVA_HOME=/opt/java/openjdk
ENV SPARK_HOME=/home/airflow/spark
ENV PATH="${JAVA_HOME}/bin:${SPARK_HOME}/bin:${PATH}"

USER airflow

# Pin to the matching Airflow constraints file. Without --constraint
# pip can silently upgrade transitive deps (sqlalchemy, alembic, flask,
# etc.) past versions Airflow tested against — same shipping-class bug
# Pass 12-13 hit when unbounded floors let `>=2.0.0` / `>=4.1.0` resolve
# to nonexistent versions. The constraint file is the upstream fence.
ARG AIRFLOW_VERSION=3.2.2
ARG PYTHON_MAJOR_MINOR=3.12

# Providers needed for the cross-stack integrations declared in the spec
# §5.3. apache-spark for SparkSubmitOperator; amazon for S3Hook (works
# with MinIO via endpoint override); openai for LiteLLM integration;
# weaviate + neo4j + postgres + redis for the per-DB Connections seeded
# by airflow-init; fab for the auth backend; common-sql for the sample
# DAG's SQLExecuteQueryOperator. (LangChain runs via PythonOperator +
# langchain-openai; no apache-airflow-providers-langchain package
# exists on PyPI.)
COPY requirements.txt /tmp/airflow-providers.txt
RUN pip install --no-cache-dir -r /tmp/airflow-providers.txt \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_MAJOR_MINOR}.txt"

# SparkSubmitOperator shells out to spark-submit. The PySpark wheel provides
# the script and Spark distribution, but the Airflow image needs a stable
# SPARK_HOME/PATH plus the same S3A/Iceberg jars as the Spark worker image so
# the submit client can resolve s3a:// app resources and client-mode smokes.
ARG HADOOP_AWS_VERSION=3.4.2
ARG AWS_SDK_BUNDLE_VERSION=2.29.52
ARG ICEBERG_VERSION=1.11.0
ARG ICEBERG_SPARK_RUNTIME_ARTIFACT=iceberg-spark-runtime-4.1_2.13
ARG ICEBERG_AWS_BUNDLE_ARTIFACT=iceberg-aws-bundle
ARG HADOOP_AWS_SHA512=c64c5f39a58b2d6b1f33ff5c14f0575b57e29401cddc599899e8b4499bbe6c76225998f6a89dfb6711779057c0a638708d190d9cd6c9fc280d38045f9fbd9cbf
ARG AWS_SDK_BUNDLE_SHA512=a909eb82364c6272cd4ff67aaff4123e4e5de9205d21c27d1f286e7748780a7b4c2b570c41d184f0600fabe482b1dc3f28d9c60ebbd4b8795666e45ecfb4802a
ARG ICEBERG_SPARK_RUNTIME_SHA512=f4620bb2d20777146a769a8a939a5bed658e1d5708b6a5155e1f6e831c0bfd29bd1f82618dc59bc0f30399ffcb03add3e66656286692dc6c2be94e4e1f4e7479
ARG ICEBERG_AWS_BUNDLE_SHA512=bb50f7dc5f36a001efecf15e4d03eff955466f06557261fbacdd7fe0d88113c82e0b9b2af96cd554cef4a1d4c2348fc950270bc3f51cf6bf1e4526f46ce336a0

COPY --chown=airflow:0 lakehouse-smoke/LakehouseSmoke.java /tmp/atlas-lakehouse-smoke/LakehouseSmoke.java

RUN set -eux; \
    pyspark_home="$(python -c 'from pathlib import Path; import pyspark; print(Path(pyspark.__file__).resolve().parent)')"; \
    ln -sfn "${pyspark_home}" "${SPARK_HOME}"; \
    jars_dir="${pyspark_home}/jars"; \
    curl -fsSL -o "${jars_dir}/hadoop-aws-${HADOOP_AWS_VERSION}.jar" \
        "https://repo.maven.apache.org/maven2/org/apache/hadoop/hadoop-aws/${HADOOP_AWS_VERSION}/hadoop-aws-${HADOOP_AWS_VERSION}.jar"; \
    echo "${HADOOP_AWS_SHA512}  ${jars_dir}/hadoop-aws-${HADOOP_AWS_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o "${jars_dir}/bundle-${AWS_SDK_BUNDLE_VERSION}.jar" \
        "https://repo.maven.apache.org/maven2/software/amazon/awssdk/bundle/${AWS_SDK_BUNDLE_VERSION}/bundle-${AWS_SDK_BUNDLE_VERSION}.jar"; \
    echo "${AWS_SDK_BUNDLE_SHA512}  ${jars_dir}/bundle-${AWS_SDK_BUNDLE_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o "${jars_dir}/${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar" \
        "https://repo.maven.apache.org/maven2/org/apache/iceberg/${ICEBERG_SPARK_RUNTIME_ARTIFACT}/${ICEBERG_VERSION}/${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar"; \
    echo "${ICEBERG_SPARK_RUNTIME_SHA512}  ${jars_dir}/${ICEBERG_SPARK_RUNTIME_ARTIFACT}-${ICEBERG_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL -o "${jars_dir}/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar" \
        "https://repo.maven.apache.org/maven2/org/apache/iceberg/${ICEBERG_AWS_BUNDLE_ARTIFACT}/${ICEBERG_VERSION}/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar"; \
    echo "${ICEBERG_AWS_BUNDLE_SHA512}  ${jars_dir}/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar" | sha512sum -c -; \
    mkdir -p /opt/airflow/atlas-jars /tmp/atlas-lakehouse-smoke/classes; \
    javac -cp "${jars_dir}/*" -d /tmp/atlas-lakehouse-smoke/classes /tmp/atlas-lakehouse-smoke/LakehouseSmoke.java; \
    jar cf /opt/airflow/atlas-jars/atlas-lakehouse-smoke.jar -C /tmp/atlas-lakehouse-smoke/classes .; \
    rm -rf /tmp/atlas-lakehouse-smoke
