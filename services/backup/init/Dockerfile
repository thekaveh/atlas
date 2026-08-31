ARG BASE_IMAGE=postgres:17.10-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193
FROM ${BASE_IMAGE}

# Manifest authentication uses OpenSSL. Resolve it during the image build so
# backup and restore execution never depend on a mutable package repository.
RUN apk add --no-cache openssl=3.5.8-r0
