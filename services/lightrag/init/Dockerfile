ARG BASE_IMAGE=alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
FROM ${BASE_IMAGE}

RUN apk add --no-cache \
    bash=5.3.9-r1 \
    ca-certificates=20260909-r0 \
    curl=8.22.0-r0 \
    jq=1.8.2-r0 \
    postgresql17-client=17.11-r0 \
    python3=3.14.8-r0
