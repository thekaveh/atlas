ARG BASE_IMAGE=${LOCAL_DEEP_RESEARCHER_IMAGE:-python:3.11.15-slim}
FROM ${BASE_IMAGE}

# Set working directory
WORKDIR /app

# Install system dependencies (LDR is DB-free — no postgresql-client/psql).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster Python package management (pinned so the dependency
# installer itself is deterministic across rebuilds).
RUN pip install --no-cache-dir uv==0.11.19

# Create necessary directories
RUN mkdir -p /app/data /app/config /app/repo

# Copy initialization scripts
COPY scripts/ /app/scripts/
COPY config/ /app/config/

# Make scripts executable
RUN chmod +x /app/scripts/*.sh /app/scripts/*.py

# Create healthcheck script
RUN echo '#!/bin/bash\ncurl -f http://localhost:2024/docs || exit 1' > /app/scripts/healthcheck.sh && \
    chmod +x /app/scripts/healthcheck.sh

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 2024

# Health check
# start-period bumped to 300s because the entrypoint clones the upstream
# LDR repo + `uv pip install` ~72 packages (numpy, lxml, langchain,
# langgraph, openai, tiktoken, …) on first launch. On a clean machine
# this routinely takes 2-5 minutes over the network; 60s flipped the
# container to ``unhealthy`` long before the langgraph server bound to
# :2024. Subsequent restarts hit the cached venv and pass in <10s, so
# the higher ceiling costs nothing in steady state.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD /app/scripts/healthcheck.sh

# Run initialization and start the service
CMD ["/app/scripts/docker-entrypoint.sh"]
