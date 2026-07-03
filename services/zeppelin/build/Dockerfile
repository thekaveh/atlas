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

FROM ${BASE_IMAGE}

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends openjdk-17-jre-headless ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /opt/java; \
    ln -sfn "$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")" /opt/java/openjdk

COPY --from=spark-runtime /opt/spark /opt/spark

ENV JAVA_HOME=/opt/java/openjdk
ENV SPARK_HOME=/opt/spark
ENV PATH="/opt/java/openjdk/bin:/opt/spark/bin:${PATH}"

USER 1000
