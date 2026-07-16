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

# Switch to root to install system packages
USER root

# Install system dependencies, including OpenJDK 17 for the Almond Scala kernel
# (Almond runs on the JVM; JDK 17 covers Scala 2.13 + Scala 3). The headless
# package is ~120 MB smaller than the full openjdk-17-jdk.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    vim \
    openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

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
      > /usr/local/bin/cs \
 && chmod +x /usr/local/bin/cs

# Switch back to jovyan user for Python package installation
USER ${NB_UID}

WORKDIR /home/jovyan

# Copy requirements file
COPY requirements.txt /tmp/requirements.txt
COPY atlas_finance /home/jovyan/atlas_finance

# PyG family packages require torch at metadata-generation time. Install the
# matching CPU trio first so the general PyPI index cannot pull CUDA runtimes.
RUN pip install --no-cache-dir \
      torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 \
      --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ml-lab NLP-task assets: spaCy language model + NLTK VADER lexicon. These are
# bundled separately from their respective pip packages (spacy / nltk) and are
# required by ml-lab's text_classification-agnews + sentiment_classification-vader
# Tier-A notebooks. ~14 MB total.
RUN python -m spacy download en_core_web_sm \
 && python -c "import nltk; nltk.download('vader_lexicon', quiet=True)"

ENV PYTHONPATH=/home/jovyan:${PYTHONPATH}

# Install Almond Scala kernels for both Scala 2.13 and Scala 3. Each kernel
# materialises ~150-200 MB of dependencies under ~/.cache/coursier; total
# Scala-toolchain footprint after the apt + cs + almond steps is ~600 MB.
# The kernels register themselves at /opt/conda/share/jupyter/kernels/.
RUN cs launch --fork almond:${ALMOND_VERSION} --scala ${ALMOND_SCALA_2_VERSION} -- \
        --install --display-name "Scala 2.13" --id scala213 --force \
 && cs launch --fork almond:${ALMOND_VERSION} --scala ${ALMOND_SCALA_3_VERSION} -- \
        --install --display-name "Scala 3" --id scala3 --force

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
