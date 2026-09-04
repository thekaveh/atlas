# Init-container exception to the stack's "vanilla alpine + apk add" rule:
# this init needs pinned psycopg2-binary==2.9.9 and PyYAML==6.0.2. The
# psycopg2-binary wheel is glibc-only (incompatible with musl/alpine),
# and pinning matters because the script's SQL/YAML output is consumed by
# litellm's runtime config. Python-slim avoids the apk + build-deps gymnastics.
FROM python:3.12.14-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

# Debian fixes arrive by deliberately refreshing this pinned base digest. A
# mutable full-distribution upgrade would make the same source non-reproducible;
# the required final-image scan gates reviewed base refreshes.

# psycopg2-binary keeps the image small (no system libpq build chain), while
# PyYAML renders config.yaml. These exact pins mirror the repository-owned,
# vulnerability-audited shared init closure in
# services/requirements-init-locked.txt.
RUN pip install --no-cache-dir \
    psycopg2-binary==2.9.9 \
    PyYAML==6.0.2

WORKDIR /app

# Scripts are bind-mounted at runtime (see docker-compose.yml).

ENTRYPOINT ["python", "/scripts/init.py"]
