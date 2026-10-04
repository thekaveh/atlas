ARG BASE_IMAGE=postgres:17.11-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
# The MinIO client comes from the maintained pgsty fork (#1288), the same
# digest-pinned image the stack's minio-init uses. The official minio/mc
# release is abandoned and its Go 1.24.6 toolchain carries fixable findings.
ARG MC_IMAGE=pgsty/mc:RELEASE.2026-09-16T00-00-00Z@sha256:cfc83108c3abb371f8fb84d99c1fdc88f8c237e022409b0081fb7c0a3be634dd
FROM ${MC_IMAGE} AS mc

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

# Baked, not downloaded: the statically linked client is taken from the
# digest-pinned image above, and the entrypoint verifies its checksum and
# version at start, failing closed if it is missing or different.
COPY --from=mc /usr/bin/mc /usr/local/bin/mc
