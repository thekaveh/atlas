ARG BASE_IMAGE=apache/zeppelin:0.12.1
ARG SPARK_IMAGE=apache/spark:4.1.2

FROM ${SPARK_IMAGE} AS spark-runtime

USER root

ARG HADOOP_AWS_VERSION=3.4.2
ARG AWS_SDK_BUNDLE_VERSION=2.29.52
ARG ICEBERG_VERSION=1.11.0
ARG ICEBERG_SPARK_RUNTIME_ARTIFACT=iceberg-spark-runtime-4.1_2.13
ARG ICEBERG_AWS_BUNDLE_ARTIFACT=iceberg-aws-bundle
ARG HADOOP_AWS_SHA512=c64c5f39a58b2d6b1f33ff5c14f0575b57e29401cddc599899e8b4499bbe6c76225998f6a89dfb6711779057c0a638708d190d9cd6c9fc280d38045f9fbd9cbf
ARG AWS_SDK_BUNDLE_SHA512=a909eb82364c6272cd4ff67aaff4123e4e5de9205d21c27d1f286e7748780a7b4c2b570c41d184f0600fabe482b1dc3f28d9c60ebbd4b8795666e45ecfb4802a
ARG ICEBERG_SPARK_RUNTIME_SHA512=f4620bb2d20777146a769a8a939a5bed658e1d5708b6a5155e1f6e831c0bfd29bd1f82618dc59bc0f30399ffcb03add3e66656286692dc6c2be94e4e1f4e7479
ARG ICEBERG_AWS_BUNDLE_SHA512=bb50f7dc5f36a001efecf15e4d03eff955466f06557261fbacdd7fe0d88113c82e0b9b2af96cd554cef4a1d4c2348fc950270bc3f51cf6bf1e4526f46ce336a0

RUN set -eux; \
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
    echo "${ICEBERG_AWS_BUNDLE_SHA512}  /opt/spark/jars/${ICEBERG_AWS_BUNDLE_ARTIFACT}-${ICEBERG_VERSION}.jar" | sha512sum -c -

# The same jackson-core and jackson-databind 2.21.2 -> 2.21.7 swap as
# services/spark/build/Dockerfile, which explains it. The final stage copies
# this stage's /opt/spark, so the 2.21.2 jars never reach the Zeppelin image.
ARG SPARK_JACKSON_VERSION=2.21.7
ARG SPARK_JACKSON_CORE_SHA512=a4f38908ebf7cb3da776955c398c7b55b0c056ae7265a511b4a2a37ef5a4c6fed433d611ee4f3a1071aa966cc5d16b1fcb95a0e7d672002ece9d89367a4f8f85
ARG SPARK_JACKSON_DATABIND_SHA512=3485a31a4815ef2577b91667768eebb10bea7ece6b1b7cc0fa3c06f2746c003f4f32c19c52cc30ad13813eff9941ae212caffa9353c717d00947ab80ecc76bd3
RUN set -eux; \
    cd /opt/spark/jars; \
    for old in jackson-core-2.21.2.jar jackson-databind-2.21.2.jar; do \
        test -f "$old"; \
        rm -f "$old"; \
    done; \
    curl -fsSL --retry 5 -o jackson-core-${SPARK_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-core/${SPARK_JACKSON_VERSION}/jackson-core-${SPARK_JACKSON_VERSION}.jar"; \
    echo "${SPARK_JACKSON_CORE_SHA512}  jackson-core-${SPARK_JACKSON_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL --retry 5 -o jackson-databind-${SPARK_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-databind/${SPARK_JACKSON_VERSION}/jackson-databind-${SPARK_JACKSON_VERSION}.jar"; \
    echo "${SPARK_JACKSON_DATABIND_SHA512}  jackson-databind-${SPARK_JACKSON_VERSION}.jar" | sha512sum -c -; \
    chmod 0644 jackson-core-${SPARK_JACKSON_VERSION}.jar jackson-databind-${SPARK_JACKSON_VERSION}.jar

FROM ${BASE_IMAGE}

USER root

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends; \
    apt-get install -y --no-install-recommends openjdk-17-jre-headless ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /opt/java; \
    ln -sfn "$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")" /opt/java/openjdk

# The stock image bundles interpreters and plugins that Atlas never configures,
# and they carry their own shaded jackson-databind 2.18.3 (CVE-2026-68497),
# JLine (CVE-2026-77422) and jackson-core 2.18.3 (CVE-2026-89407 and
# CVE-2026-89425 in BigQuery and the Azure and GCS notebook stores). Atlas
# notebooks use only %spark, %spark.pyspark, %spark.sql, %md and JDBC
# (%postgres, %trino), and keep Zeppelin's default local notebook storage, so
# no cloud notebook-store plugin is ever loaded. The server's own Jackson 2.18.3
# and BouncyCastle 1.80 jars in lib/ have fixes Zeppelin 0.12.1 never shipped:
# CVE-2026-68497 for Jackson, and CVE-2026-8763 and CVE-2026-13506 for
# BouncyCastle. Each library family is swapped as a whole, at a patch release
# of the same minor for Jackson, so the jars stay mutually compatible (#1312).
# `test -d` and `test -f` fail the build if a base-image bump moves or renames
# anything, so each refresh re-reviews this list rather than silently skipping
# it. These paths live in the base image's layers, so the delete leaves them
# only as whiteouts that the running container cannot load.
# curl retries a Maven Central 429 with backoff; seven fetches in one step made
# that throttle a realistic CI failure.
# Bump procedure: fetch the new jars, check them against Maven's .sha1 and
# .sha512, run `sha512sum <jar>`, and replace the matching ARG default.
ARG ZEPPELIN_JACKSON_VERSION=2.18.11
ARG ZEPPELIN_BOUNCYCASTLE_VERSION=1.86
ARG JACKSON_ANNOTATIONS_SHA512=13f46940bc3f515610e35816a440a1e17986291a950b2ecc016347ed2908f6f410300b20318a73c7f7c7420fdee228a45d7e673f90fd756ed5d4d223d9d54cfb
ARG JACKSON_CORE_SHA512=af002b10ee66ca91d7c500ee1ded28180f2b53fe543040cdf9b9a9ff87f5507f7d0652bfbf2cae792c0059ed2c335825f5e2c2b4f37678cd10ec212a608a4709
ARG JACKSON_DATABIND_SHA512=171e95ce8d9efda2c6806c05a4d0e62642234c451476d0d1e8b770d88c2992366dbc800729396b01ef4fe891f094005fe7ee5c5dbda7bb4153eb8dc1bf44c5ac
ARG JACKSON_JAKARTA_XMLBIND_SHA512=8b6bdd508288769b91c0aac2a9c11573177f2aebaa4dd0e6f853a1f09ac6fbe21a71a68c28c1ff36d71f3b90674efd820626e8632097101f08a5c35b23bc022e
ARG BCPROV_SHA512=4502851281929ef645316ae96ad7d6db6a54d11ce024a66bec654c2eef62b80f174b67a2a15de86b2698f33b696459e21ecfec4a846350f4297394e9a331c43b
ARG BCPKIX_SHA512=2923c0da3848b47d1953bdba3ae50441f3a3416f3646a26244c080414f31fbde875e4e50619a0ef34670c877367a45438a346260fc8b047e753b553debd40b37
ARG BCUTIL_SHA512=aeb9138f96d5c48491974974534bb3b07931a18d36ccbed828940ae0361f5ef8f5b0ad6c776a1aebb4d2c0416edf8b7d143d821bfe7136d7948c4e5807b1c426

RUN set -eux; \
    cd /opt/zeppelin; \
    for unused in \
        interpreter/alluxio \
        interpreter/bigquery \
        interpreter/cassandra \
        interpreter/elasticsearch \
        interpreter/neo4j \
        interpreter/r \
        interpreter/sparql \
        plugins/Launcher/DockerInterpreterLauncher \
        plugins/Launcher/K8sStandardInterpreterLauncher \
        plugins/NotebookRepo/AzureNotebookRepo \
        plugins/NotebookRepo/GCSNotebookRepo \
        plugins/NotebookRepo/S3NotebookRepo; \
    do \
        test -d "$unused"; \
        rm -rf "$unused"; \
    done; \
    for old in \
        lib/jackson-annotations-2.18.3.jar \
        lib/jackson-core-2.18.3.jar \
        lib/jackson-databind-2.18.3.jar \
        lib/jackson-module-jakarta-xmlbind-annotations-2.18.3.jar \
        lib/bcprov-jdk18on-1.80.2.jar \
        lib/bcpkix-jdk18on-1.80.jar \
        lib/bcutil-jdk18on-1.80.2.jar; \
    do \
        test -f "$old"; \
        rm -f "$old"; \
    done; \
    curl -fsSL --retry 5 -o lib/jackson-annotations-${ZEPPELIN_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-annotations/${ZEPPELIN_JACKSON_VERSION}/jackson-annotations-${ZEPPELIN_JACKSON_VERSION}.jar"; \
    echo "${JACKSON_ANNOTATIONS_SHA512}  lib/jackson-annotations-${ZEPPELIN_JACKSON_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL --retry 5 -o lib/jackson-core-${ZEPPELIN_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-core/${ZEPPELIN_JACKSON_VERSION}/jackson-core-${ZEPPELIN_JACKSON_VERSION}.jar"; \
    echo "${JACKSON_CORE_SHA512}  lib/jackson-core-${ZEPPELIN_JACKSON_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL --retry 5 -o lib/jackson-databind-${ZEPPELIN_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-databind/${ZEPPELIN_JACKSON_VERSION}/jackson-databind-${ZEPPELIN_JACKSON_VERSION}.jar"; \
    echo "${JACKSON_DATABIND_SHA512}  lib/jackson-databind-${ZEPPELIN_JACKSON_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL --retry 5 -o lib/jackson-module-jakarta-xmlbind-annotations-${ZEPPELIN_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/module/jackson-module-jakarta-xmlbind-annotations/${ZEPPELIN_JACKSON_VERSION}/jackson-module-jakarta-xmlbind-annotations-${ZEPPELIN_JACKSON_VERSION}.jar"; \
    echo "${JACKSON_JAKARTA_XMLBIND_SHA512}  lib/jackson-module-jakarta-xmlbind-annotations-${ZEPPELIN_JACKSON_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL --retry 5 -o lib/bcprov-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/bouncycastle/bcprov-jdk18on/${ZEPPELIN_BOUNCYCASTLE_VERSION}/bcprov-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar"; \
    echo "${BCPROV_SHA512}  lib/bcprov-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL --retry 5 -o lib/bcpkix-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/bouncycastle/bcpkix-jdk18on/${ZEPPELIN_BOUNCYCASTLE_VERSION}/bcpkix-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar"; \
    echo "${BCPKIX_SHA512}  lib/bcpkix-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar" | sha512sum -c -; \
    curl -fsSL --retry 5 -o lib/bcutil-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/org/bouncycastle/bcutil-jdk18on/${ZEPPELIN_BOUNCYCASTLE_VERSION}/bcutil-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar"; \
    echo "${BCUTIL_SHA512}  lib/bcutil-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar" | sha512sum -c -; \
    chmod 0644 lib/jackson-*-${ZEPPELIN_JACKSON_VERSION}.jar lib/bc*-jdk18on-${ZEPPELIN_BOUNCYCASTLE_VERSION}.jar

COPY --from=spark-runtime /opt/spark /opt/spark

# PySpark 4.1 needs Python 3.10 or newer, and the stock image's conda envs are
# 3.7 (the default `python`) and 3.9 (#1314). The Spark cluster's executors run
# apache/spark:4.1.2's Python 3.10, and the driver's minor version must match
# theirs for Python UDFs and RDD work. This Ubuntu 20.04 base has no system 3.10
# and cannot run the Spark image's own build, so a conda-forge CPython 3.10 env
# is installed from an explicit lock: exact package URLs with md5, no solve.
# seed-spark-interpreter.py points PYSPARK_DRIVER_PYTHON at it.
ARG TARGETARCH
COPY pyspark-env/ /tmp/atlas-pyspark-env/
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) conda_subdir=linux-64 ;; \
      arm64) conda_subdir=linux-aarch64 ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY=false /opt/conda/bin/conda create -y \
      -p /opt/conda/envs/pyspark --file "/tmp/atlas-pyspark-env/${conda_subdir}.txt"; \
    /opt/conda/bin/conda clean -afy; \
    rm -rf /tmp/atlas-pyspark-env; \
    /opt/conda/envs/pyspark/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 10), sys.version'

ENV JAVA_HOME=/opt/java/openjdk
ENV SPARK_HOME=/opt/spark
ENV PATH="/opt/java/openjdk/bin:/opt/spark/bin:${PATH}"

USER 1000
