ARG BASE_IMAGE=postgres:17.10-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193
FROM ${BASE_IMAGE}

# The pinned Postgres base lags Alpine security updates, so the final-image
# gate sees fixable findings the pin alone cannot clear (seven util-linux
# libuuid advisories on 3.24.1, #1002). Take the published updates the same
# way services/asset-baker, services/spark and services/airflow do; this
# trades byte-for-byte base reproducibility for a patched runtime.
#
# Manifest authentication uses OpenSSL. Resolve it during the image build so
# backup and restore execution never depend on a mutable package repository.
RUN apk upgrade --no-cache \
    && apk add --no-cache openssl=3.5.8-r0
