ARG BASE_IMAGE=apache/iceberg-rest-fixture:1.10.1
FROM ${BASE_IMAGE}

ARG POSTGRES_JDBC_VERSION=42.7.12
USER root
ADD https://repo1.maven.org/maven2/org/postgresql/postgresql/${POSTGRES_JDBC_VERSION}/postgresql-${POSTGRES_JDBC_VERSION}.jar /usr/lib/iceberg-rest/postgresql.jar
RUN chmod 0644 /usr/lib/iceberg-rest/postgresql.jar \
    && chown iceberg:iceberg /usr/lib/iceberg-rest/postgresql.jar
USER iceberg

CMD ["java", "-cp", "/usr/lib/iceberg-rest/postgresql.jar:/usr/lib/iceberg-rest/iceberg-rest-adapter.jar", "org.apache.iceberg.rest.RESTCatalogServer"]
