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
# The dev auto-reloader is OPT-IN (#679): the production/consumer image runs
# plain uvicorn so host-side git churn in a bind-mounted plugin dir cannot
# restart or crash-loop the backend. configure-backend.sh appends --reload only
# when BACKEND_DEV_RELOAD=true (uvicorn[standard] brings watchfiles). Consumers
# editing plugins live can opt in; the plugin seam installs at boot, so a
# container recreate — not a hot reload — is the correct pickup path.
ENTRYPOINT ["/usr/local/bin/configure-backend.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
