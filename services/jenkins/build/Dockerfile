ARG BASE_IMAGE=jenkins/jenkins:lts-jdk21@sha256:c1e4c349365f6d16d88595b2c5f7e8ff39b8ae1d061f62420bac193b4b9616d0

FROM ${BASE_IMAGE}

USER root

ARG MINIO_MC_VERSION=RELEASE.2025-08-13T08-35-41Z
ARG MINIO_MC_SHA256_AMD64=01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891
ARG MINIO_MC_SHA256_ARM64=14c8c9616cfce4636add161304353244e8de383b2e2752c0e9dad01d4c27c12c
# The upstream image bundles git-lfs 3.7.1 built with Go 1.25.3, which the
# final-image gate reports with 39 fixed findings (x/crypto, x/net, stdlib).
# Replace it with the current release, checksum-verified the same way mc is
# (#1002); published sums from the release's sha256sums.asc.
ARG GIT_LFS_VERSION=3.8.0
ARG GIT_LFS_SHA256_AMD64=e455e00f15d9b95661b8d53498ffb0c3367962cf1ec73c31ab7369516cd6ab8d
ARG GIT_LFS_SHA256_ARM64=ac9c8efac980bb0505ead384d087e2acb6486fd8498691a2165fa174ec6118c2
ARG TARGETARCH

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends; \
    apt-get install -y --no-install-recommends ca-certificates curl maven; \
    rm -rf /var/lib/apt/lists/*; \
    arch="${TARGETARCH:-amd64}"; \
    case "$arch" in \
      amd64) mc_arch="amd64"; mc_sha256="${MINIO_MC_SHA256_AMD64}"; lfs_sha256="${GIT_LFS_SHA256_AMD64}" ;; \
      arm64) mc_arch="arm64"; mc_sha256="${MINIO_MC_SHA256_ARM64}"; lfs_sha256="${GIT_LFS_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/mc "https://github.com/minio/mc/releases/download/${MINIO_MC_VERSION}/mc.linux-${mc_arch}.${MINIO_MC_VERSION}"; \
    echo "${mc_sha256}  /usr/local/bin/mc" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/mc; \
    curl -fsSL -o /tmp/git-lfs.tar.gz "https://github.com/git-lfs/git-lfs/releases/download/v${GIT_LFS_VERSION}/git-lfs-linux-${mc_arch}-v${GIT_LFS_VERSION}.tar.gz"; \
    echo "${lfs_sha256}  /tmp/git-lfs.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/git-lfs.tar.gz -C /tmp "git-lfs-${GIT_LFS_VERSION}/git-lfs"; \
    install -m 0755 "/tmp/git-lfs-${GIT_LFS_VERSION}/git-lfs" /usr/local/bin/git-lfs; \
    rm -rf /tmp/git-lfs.tar.gz "/tmp/git-lfs-${GIT_LFS_VERSION}"; \
    git-lfs version; \
    echo 2.0 > /usr/share/jenkins/ref/jenkins.install.UpgradeWizard.state

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

USER jenkins
