ARG BASE_IMAGE=jenkins/jenkins:lts-jdk21@sha256:c1e4c349365f6d16d88595b2c5f7e8ff39b8ae1d061f62420bac193b4b9616d0
# The MinIO client comes from the maintained pgsty fork (#1288), the same
# digest-pinned image the stack's minio-init uses. The official minio/mc
# release is abandoned and its Go 1.24.6 toolchain carries fixable findings.
ARG MC_IMAGE=pgsty/mc:RELEASE.2026-09-16T00-00-00Z@sha256:cfc83108c3abb371f8fb84d99c1fdc88f8c237e022409b0081fb7c0a3be634dd

FROM ${MC_IMAGE} AS mc

FROM ${BASE_IMAGE}

USER root

# The upstream image bundles git-lfs 3.7.1 built with Go 1.25.3, which the
# final-image gate reports with 39 fixed findings (x/crypto, x/net, stdlib).
# Replace it with the current release, checksum-verified per architecture
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
      amd64) lfs_sha256="${GIT_LFS_SHA256_AMD64}" ;; \
      arm64) lfs_sha256="${GIT_LFS_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/git-lfs.tar.gz "https://github.com/git-lfs/git-lfs/releases/download/v${GIT_LFS_VERSION}/git-lfs-linux-${arch}-v${GIT_LFS_VERSION}.tar.gz"; \
    echo "${lfs_sha256}  /tmp/git-lfs.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/git-lfs.tar.gz -C /tmp "git-lfs-${GIT_LFS_VERSION}/git-lfs"; \
    install -m 0755 "/tmp/git-lfs-${GIT_LFS_VERSION}/git-lfs" /usr/local/bin/git-lfs; \
    rm -rf /tmp/git-lfs.tar.gz "/tmp/git-lfs-${GIT_LFS_VERSION}"; \
    git-lfs version; \
    echo 2.0 > /usr/share/jenkins/ref/jenkins.install.UpgradeWizard.state

# The statically linked client from the digest-pinned image above: the index
# digest fixes the exact amd64 and arm64 binaries, so no download or separate
# checksum is needed.
COPY --from=mc /usr/bin/mc /usr/local/bin/mc

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

USER jenkins
