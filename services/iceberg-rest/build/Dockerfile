ARG BASE_IMAGE=apache/iceberg-rest-fixture:1.10.1
FROM ${BASE_IMAGE}

ARG POSTGRES_JDBC_VERSION=42.7.12
ARG POSTGRES_JDBC_SHA512=3759e7160591863e5100361298943df94488a6e4ee03936d20723638142fe8038c9a6a47fa8ee7e424f4ef09bb351edc89dfe2ae4acdfc0f92699a8b00196c5c
USER root
RUN set -eux; \
    curl -fsSL \
      "https://repo1.maven.org/maven2/org/postgresql/postgresql/${POSTGRES_JDBC_VERSION}/postgresql-${POSTGRES_JDBC_VERSION}.jar" \
      -o /usr/lib/iceberg-rest/postgresql.jar; \
    echo "${POSTGRES_JDBC_SHA512}  /usr/lib/iceberg-rest/postgresql.jar" | sha512sum -c -; \
    chmod 0644 /usr/lib/iceberg-rest/postgresql.jar; \
    chown iceberg:iceberg /usr/lib/iceberg-rest/postgresql.jar
USER iceberg

CMD ["java", "-cp", "/usr/lib/iceberg-rest/postgresql.jar:/usr/lib/iceberg-rest/iceberg-rest-adapter.jar", "org.apache.iceberg.rest.RESTCatalogServer"]
