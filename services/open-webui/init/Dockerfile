# Init-container exception to the stack's "vanilla alpine + apk add" rule:
# this init needs psycopg2-binary + PyJWT, both of which ship glibc-only
# wheels (musl/alpine incompatible). Python-slim avoids the apk + build-deps
# gymnastics. Override via OPEN_WEB_UI_INIT_IMAGE in .env if needed.
#
# Pin matches the python:3.12.7-slim already used by litellm/init. The
# previous default was `python:3.12-slim` — a floating patch tag that
# silently rolled forward, leaving the two
# init containers out of step (litellm/init explicitly pinned 3.12.7
# for the same reproducibility reason). psycopg2-binary 2.9.9 ships
# cp312 wheels so the original 3.11 pin was no longer load-bearing.
ARG BASE_IMAGE=python:3.12.7-slim
FROM ${BASE_IMAGE}

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Pin to current stable releases so a future upstream regression doesn't
# silently land at docker build time (open-webui-init isn't built by CI).
# Floors verified satisfiable against PyPI on 2026-06-07.
RUN pip install --no-cache-dir \
    requests==2.32.3 \
    psycopg2-binary==2.9.9 \
    PyJWT==2.10.1

COPY scripts/ /app/scripts/
RUN chmod +x /app/scripts/*.py

CMD ["sh", "-c", "python3 /app/scripts/register-tools.py && python3 /app/scripts/register-functions.py"]
