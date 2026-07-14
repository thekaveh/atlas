# Fallback mirrors BACKEND_IMAGE in service.yml — compose overrides via
# build.args; CI build-validation builds with this default, so keep in sync.
ARG BASE_IMAGE=${BACKEND_IMAGE:-python:3.12.13}
FROM ${BASE_IMAGE}

WORKDIR /app

# Install uv (pinned so the dependency installer itself is deterministic —
# an unpinned uv resolves requirements differently across releases).
RUN pip install --no-cache-dir uv==0.11.19

# Copy requirements first for better layer caching
COPY ./app/requirements.txt .

# Install dependencies using uv with --system flag
RUN uv pip install --system --no-cache-dir -r requirements.txt

# Copy application code
COPY ./app .

# Copy configuration script
COPY ./configure-backend.sh /usr/local/bin/configure-backend.sh

# Create a non-root user with a writable home. Runtime dependencies such as
# Chonkie/tokie download tokenizer metadata into the user cache.
RUN groupadd --system --gid 1001 appgroup && \
    useradd --system --create-home --home-dir /home/appuser \
      --uid 1001 --gid appgroup appuser && \
    chmod +x /usr/local/bin/configure-backend.sh

ENV HOME=/home/appuser \
    XDG_CACHE_HOME=/home/appuser/.cache

# Switch to the non-root user
USER appuser

# Health check (using httpx which is already a dependency)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/ready').raise_for_status()"

# Expose API port
EXPOSE 8000

# Run the application with dynamic configuration.
# --reload hot-reloads on edits — the compose fragment bind-mounts
# ./app/app onto /app, so host-side edits are picked up without a
# rebuild. uvicorn[standard] in requirements.txt brings watchfiles.
ENTRYPOINT ["/usr/local/bin/configure-backend.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
