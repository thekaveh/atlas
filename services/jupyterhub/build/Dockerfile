# Default mirrors JUPYTERHUB_IMAGE in service.yml — compose overrides via
# build.args; CI build-validation builds with this default, so keep in sync.
ARG BASE_IMAGE=quay.io/jupyter/datascience-notebook:python-3.11.10
FROM ${BASE_IMAGE}

# Auto-populated by buildx (and `docker build` since v20.10) with the
# target architecture (`amd64`, `arm64`). Used below to pick the
# arch-matching Coursier binary URL.
ARG TARGETARCH

# Almond (the Scala kernel for Jupyter) and its Coursier installer are baked at
# image-build time. Bumping either value rebuilds the kernel cache, so pin
# explicitly rather than `latest`. Versions current as of 2026-06-02.
ARG ALMOND_VERSION=0.14.5
ARG ALMOND_SCALA_2_VERSION=2.13.16
ARG ALMOND_SCALA_3_VERSION=3.4.3
ARG COURSIER_SHA256=7aa975f12469726d6bb87852107afe0b8d6f3c82b12a49ef43cc6647c4e057ca
ARG NPM_VERSION=11.19.1
ARG JULIA_VERSION=1.12.7
ARG JULIA_SHA256_AMD64=4e7e9e776634d24835250de67cde39b0d4af15bc432eb20697e6be6c28ea69e8
ARG JULIA_SHA256_ARM64=9243c0b524c7f300883240a1ee5ea3916a30e070bff718acf8ccaee31a731ef2

# Switch to root to install system packages
USER root

# Install system dependencies, including OpenJDK 17 for the Almond Scala kernel
# (Almond runs on the JVM; JDK 17 covers Scala 2.13 + Scala 3). The headless
# package is ~120 MB smaller than the full openjdk-17-jdk.
RUN apt-get update && \
    apt-get upgrade --yes && \
    apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    vim \
    openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# Node 22.9 in the pinned base supports npm 11. npm 11.19.1 updates the
# vulnerable tar/glob/minimatch/sigstore dependency closure while retaining the
# npm CLI that notebook users expect from the data-science image.
RUN npm install --global "npm@${NPM_VERSION}" \
 && npm cache clean --force

# The base image's Julia 1.11.1 standard-library JLLs are no longer patchable
# through Pkg. Replace only the Julia runtime with the current checksummed
# release while retaining the upstream Python 3.11 image and notebook stack.
RUN case "${TARGETARCH}" in \
      amd64) julia_arch=x86_64; julia_dir=x64; julia_sha="${JULIA_SHA256_AMD64}" ;; \
      arm64) julia_arch=aarch64; julia_dir=aarch64; julia_sha="${JULIA_SHA256_ARM64}" ;; \
      *) echo "unsupported Julia architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fL \
      "https://julialang-s3.julialang.org/bin/linux/${julia_dir}/1.12/julia-${JULIA_VERSION}-linux-${julia_arch}.tar.gz" \
      -o /tmp/julia.tar.gz \
 && echo "${julia_sha}  /tmp/julia.tar.gz" | sha256sum -c - \
 && rm -rf /opt/julia-1.11.1 \
 && tar -xzf /tmp/julia.tar.gz -C /opt \
 && ln -sfn "/opt/julia-${JULIA_VERSION}/bin/julia" /usr/local/bin/julia \
 && rm /tmp/julia.tar.gz

# Coursier — Scala's launcher/dep-resolver. Almond's install path uses
# `cs launch almond:<ver> --scala <ver> -- --install` to register a Jupyter
# kernel.
#
# We install the arch-neutral bootstrap launcher (`coursier`) rather than the
# native-image binaries (`cs-x86_64-pc-linux.gz` / `cs-aarch64-apple-darwin.gz`
# etc.) because Coursier doesn't publish a Linux arm64 native binary — using
# the amd64 binary on an arm64 image triggers
# `rosetta error: failed to open elf at /lib64/ld-linux-x86-64.so.2` at the
# `cs launch --fork` step. The bootstrap launcher is a thin JAR wrapper that
# runs on the JVM we already installed above; it works on every arch.
RUN curl -fL "https://github.com/coursier/coursier/releases/download/v2.1.24/coursier" \
      -o /tmp/coursier \
 && echo "${COURSIER_SHA256}  /tmp/coursier" | sha256sum -c - \
 && install -m 0755 /tmp/coursier /usr/local/bin/cs \
 && rm /tmp/coursier

# Switch back to jovyan user for Python package installation
USER ${NB_UID}

WORKDIR /home/jovyan

# Copy requirements file
COPY requirements.txt /tmp/requirements.txt
COPY requirements-locked.txt /tmp/requirements-locked.txt
COPY atlas_finance /home/jovyan/atlas_finance

# PyG family packages require torch at metadata-generation time. Install the
# matching CPU trio first so the general PyPI index cannot pull CUDA runtimes.
RUN pip install --no-cache-dir \
      torch==2.13.0 torchvision==0.28.0 \
      --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir \
      -r /tmp/requirements.txt -c /tmp/requirements-locked.txt

# ml-eng-lab NLP-task assets: the spaCy wheel and VADER ZIP are exact,
# hash-required projections of the parent repository's current contract.
COPY --chown=${NB_UID}:${NB_GID} nlp-model-requirements.txt /tmp/nlp-model-requirements.txt
COPY --chown=${NB_UID}:${NB_GID} nlp-assets.toml /tmp/nlp-assets.toml
COPY --chown=${NB_UID}:${NB_GID} install_nlp_assets.py /tmp/install_nlp_assets.py
RUN python -m pip install --no-cache-dir --no-deps --require-hashes -r /tmp/nlp-model-requirements.txt \
 && python /tmp/install_nlp_assets.py install --manifest /tmp/nlp-assets.toml --data-dir /home/jovyan/nltk_data \
 && python /tmp/install_nlp_assets.py verify --manifest /tmp/nlp-assets.toml --data-dir /home/jovyan/nltk_data \
 && rm -f /tmp/nlp-model-requirements.txt /tmp/nlp-assets.toml /tmp/install_nlp_assets.py

ENV NLTK_DATA=/home/jovyan/nltk_data
ENV PYTHONPATH=/home/jovyan

# Install Almond Scala kernels for both Scala 2.13 and Scala 3. Each kernel
# materialises ~150-200 MB of dependencies under ~/.cache/coursier; total
# Scala-toolchain footprint after the apt + cs + almond steps is ~600 MB.
# The kernels register themselves at /opt/conda/share/jupyter/kernels/.
RUN cs launch --fork almond:${ALMOND_VERSION} --scala ${ALMOND_SCALA_2_VERSION} \
        --force-version com.google.protobuf:protobuf-java:3.25.5 \
        --force-version at.yawk.lz4:lz4-java:1.10.3 -- \
        --install --display-name "Scala 2.13" --id scala213 --force \
 && cs launch --fork almond:${ALMOND_VERSION} --scala ${ALMOND_SCALA_3_VERSION} \
        --force-version com.google.protobuf:protobuf-java:3.25.5 \
        --force-version at.yawk.lz4:lz4-java:1.10.3 -- \
        --install --display-name "Scala 3" --id scala3 --force \
 && find /home/jovyan/.cache/coursier -type f -name '*-sources.jar' -delete \
 && rm -rf \
        /home/jovyan/.cache/coursier/v1/https/repo1.maven.org/maven2/at/yawk/lz4/lz4-java/1.8.1 \
        /home/jovyan/.cache/coursier/v1/https/repo1.maven.org/maven2/io/airlift/aircompressor/0.27 \
        /home/jovyan/.cache/coursier/v1/https/repo1.maven.org/maven2/org/lz4/lz4-java/1.8.1 \
        /home/jovyan/.cache/coursier/v1/https/repo1.maven.org/maven2/org/codehaus/plexus/plexus-utils/4.0.1 \
        /home/jovyan/.cache/coursier/v1/https/repo1.maven.org/maven2/org/jline/jline/3.24.1 \
        /home/jovyan/.cache/coursier/v1/https/repo1.maven.org/maven2/org/jline/jline/3.27.1

# Replace the stale Julia environment inherited from the upstream image with a
# repository-owned lock. Nested Manifest.toml files belong only to Julia's
# bundled tests/docs and otherwise produce duplicate, non-runtime findings.
USER root
COPY julia/Project.toml julia/Manifest.toml /opt/julia/environments/v1.12/
# Instantiation materialises the exact packages and artifacts. Disable bulk
# auto-precompilation here: it is redundant for image correctness and can take
# hours under the arm64/QEMU leg of required build validation. Loading IJulia
# below still validates and compiles the kernel's actual dependency path.
RUN JUPYTER_DATA_DIR=/opt/conda/share/jupyter \
    julia --project=/opt/julia/environments/v1.12 --startup-file=no -e \
      'using Pkg; Pkg.instantiate(; allow_autoprecomp=false); using IJulia; IJulia.installkernel("Julia 1.12")' \
 && rm -rf /opt/conda/share/jupyter/kernels/julia-1.11 \
 && find /opt/julia -name Manifest.toml \
      ! -path /opt/julia/environments/v1.12/Manifest.toml -delete \
 && find "/opt/julia-${JULIA_VERSION}" -name Manifest.toml -delete \
 && chown -R ${NB_UID}:${NB_GID} /opt/julia
USER ${NB_UID}

# Copy startup script
COPY scripts/startup.sh /usr/local/bin/startup.sh

# Switch to root to make startup script executable
USER root
RUN chmod +x /usr/local/bin/startup.sh

# Create directories for notebooks and shared config
RUN mkdir -p /home/jovyan/work \
    /home/jovyan/notebooks \
    /shared && \
    chown -R ${NB_UID}:${NB_GID} /home/jovyan/work /home/jovyan/notebooks /home/jovyan/atlas_finance

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8888/api || exit 1

# Switch back to jovyan user
USER ${NB_UID}

# Set working directory
WORKDIR /home/jovyan/work

# Expose Jupyter port
EXPOSE 8888

# Use startup script as entrypoint
ENTRYPOINT ["/usr/local/bin/startup.sh"]
CMD ["start-notebook.sh"]
