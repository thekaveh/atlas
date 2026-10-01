ARG BASE_IMAGE=postgres:17.11-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
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
    && apk add --no-cache openssl=3.5.9-r0
