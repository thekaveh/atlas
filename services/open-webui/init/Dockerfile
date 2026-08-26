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

# Exact pins mirror the repository-owned, vulnerability-audited shared init
# closure in services/requirements-init-locked.txt. The build context is this
# service directory, so the repository gate verifies these inline pins against
# that central lock instead of copying a file from outside the context.
RUN pip install --no-cache-dir \
    certifi==2026.7.22 \
    charset-normalizer==3.5.1 \
    idna==3.19 \
    psycopg2-binary==2.9.9 \
    PyJWT==2.13.0 \
    requests==2.33.0 \
    urllib3==2.7.0

COPY scripts/ /app/scripts/
RUN chmod +x /app/scripts/*.py

CMD ["sh", "-c", "python3 /app/scripts/register-tools.py && python3 /app/scripts/register-functions.py"]
