ARG BASE_IMAGE=${NEO4J_GRAPH_DB_IMAGE:-neo4j:5.26.30}
FROM ${BASE_IMAGE}

# Debian security fixes normally arrive through a reviewed BASE_IMAGE refresh.
# The 5.26.30 refresh (2026-09-18, #1002) cleared every jar finding and 217 of
# 223 OS findings the 5.26.27 image carried, but the newest published tag
# still lags six fixed HIGH advisories in gzip, libpcre2 and libsqlite3, so
# take the published updates the same way services/backend, services/airflow,
# services/spark and services/backup do. This trades byte-for-byte base
# reproducibility for a patched runtime; the final-image scan still gates
# every refresh.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

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
