ARG BASE_IMAGE=jenkins/jenkins:lts-jdk21

FROM ${BASE_IMAGE}

USER root

ARG MINIO_MC_VERSION=RELEASE.2025-08-13T08-35-41Z
ARG MINIO_MC_SHA256_AMD64=01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891
ARG MINIO_MC_SHA256_ARM64=14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c
ARG TARGETARCH

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl maven; \
    rm -rf /var/lib/apt/lists/*; \
    arch="${TARGETARCH:-amd64}"; \
    case "$arch" in \
      amd64) mc_arch="amd64"; mc_sha256="${MINIO_MC_SHA256_AMD64}" ;; \
      arm64) mc_arch="arm64"; mc_sha256="${MINIO_MC_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/mc "https://github.com/minio/mc/releases/download/${MINIO_MC_VERSION}/mc.linux-${mc_arch}.${MINIO_MC_VERSION}"; \
    echo "${mc_sha256}  /usr/local/bin/mc" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/mc; \
    echo 2.0 > /usr/share/jenkins/ref/jenkins.install.UpgradeWizard.state

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

USER jenkins
