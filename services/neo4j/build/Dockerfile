ARG BASE_IMAGE=${NEO4J_GRAPH_DB_IMAGE:-neo4j:5.26.31}
FROM ${BASE_IMAGE}

# Debian security fixes normally arrive through a reviewed BASE_IMAGE refresh.
# The 5.26.31 refresh (2026-09-30, #1312) drops the standalone
# jackson-databind 2.22.1 jar (CVE-2026-68497) that 5.26.30 shipped, but the
# newest published tag still lags 40 fixed HIGH findings in its Debian 13.7
# packages, so take the published updates the same way services/backend,
# services/airflow, services/spark and services/backup do. This trades
# byte-for-byte base reproducibility for a patched runtime; the final-image
# scan still gates every refresh.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# jackson-core and jackson-databind 2.22.2 in Neo4j 5.26.31's lib carry
# CVE-2026-89407, CVE-2026-89425, CVE-2026-91776 and CVE-2026-91777, fixed in
# 2.22.3. A patch release of the same minor is drop-in, so only these two jars
# move: jackson-annotations-2.22 and the 2.22.2 jaxrs and jaxb modules stay as
# shipped. A local Neo4j started on the swapped jars and answered Cypher over
# Bolt and the HTTP transaction API, with nested JSON parameters round-tripped.
# `test -f` fails the build if a Neo4j bump renames them, so each refresh
# re-reviews the swap. The base has wget but not curl. These jars live in the
# base image's layers, so the delete leaves them only as whiteouts. The jackson
# copy shaded inside parquet-jackson cannot be swapped and stays a path-scoped
# exception.
ARG NEO4J_JACKSON_VERSION=2.22.3
ARG NEO4J_JACKSON_CORE_SHA512=8d5984961b37f9570d0581a74f005442fe37c46c601ee0fe63e3695f967bd7598dd268cfb33509a289ff472385cedd99396660e31d7cb873d1e7ffff676912e8
ARG NEO4J_JACKSON_DATABIND_SHA512=2c94560ef0f01f7dd12ae0810dbcfaf80fddd889bc25f597350fb75a0fcc266fa68f5795a773f772692426ac81a6ee65ed2bde8db8184bb2788323bc95ef7ebe
RUN set -eux; \
    cd /var/lib/neo4j/lib; \
    for old in jackson-core-2.22.2.jar jackson-databind-2.22.2.jar; do \
        test -f "$old"; \
        rm -f "$old"; \
    done; \
    wget -q --tries=5 --waitretry=5 --retry-on-http-error=429,503 \
        -O jackson-core-${NEO4J_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-core/${NEO4J_JACKSON_VERSION}/jackson-core-${NEO4J_JACKSON_VERSION}.jar"; \
    echo "${NEO4J_JACKSON_CORE_SHA512}  jackson-core-${NEO4J_JACKSON_VERSION}.jar" | sha512sum -c -; \
    wget -q --tries=5 --waitretry=5 --retry-on-http-error=429,503 \
        -O jackson-databind-${NEO4J_JACKSON_VERSION}.jar \
        "https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-databind/${NEO4J_JACKSON_VERSION}/jackson-databind-${NEO4J_JACKSON_VERSION}.jar"; \
    echo "${NEO4J_JACKSON_DATABIND_SHA512}  jackson-databind-${NEO4J_JACKSON_VERSION}.jar" | sha512sum -c -; \
    chown neo4j:neo4j jackson-core-${NEO4J_JACKSON_VERSION}.jar jackson-databind-${NEO4J_JACKSON_VERSION}.jar; \
    chmod 0644 jackson-core-${NEO4J_JACKSON_VERSION}.jar jackson-databind-${NEO4J_JACKSON_VERSION}.jar

# Copy backup and restore scripts
COPY ./scripts/backup.sh /usr/local/bin/backup.sh
COPY ./scripts/restore.sh /usr/local/bin/restore.sh

# Make scripts executable
RUN chmod +x /usr/local/bin/backup.sh \
    && chmod +x /usr/local/bin/restore.sh

# Create backup directory
RUN mkdir -p /backups

# Add automatic restore script
COPY ./scripts/auto_restore.sh /usr/local/bin/auto_restore.sh
RUN chmod +x /usr/local/bin/auto_restore.sh

# Set up a cron job to run backups (optional - commented out by default)
# RUN apt-get update && apt-get install -y cron \
#    && echo "0 0 * * * /usr/local/bin/backup.sh >> /var/log/cron.log 2>&1" > /etc/cron.d/neo4j-backup \
#    && chmod 0644 /etc/cron.d/neo4j-backup \
#    && crontab /etc/cron.d/neo4j-backup

# Modify the entry point to include auto restore
COPY ./scripts/docker-entrypoint-wrapper.sh /docker-entrypoint-wrapper.sh
RUN chmod +x /docker-entrypoint-wrapper.sh

ENTRYPOINT ["tini", "-g", "--", "/docker-entrypoint-wrapper.sh"]
CMD ["neo4j"]
