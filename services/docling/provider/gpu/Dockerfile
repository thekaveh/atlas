# Default must match services/docling/service.yml::images and
# services/docling/compose.yml so a hand-invoked `docker buildx build .`
# pulls the same image the bootstrapper-managed compose flow uses.
ARG BASE_IMAGE=pytorch/pytorch:2.13.0-cuda12.6-cudnn9-runtime@sha256:6acf597eeb8e376a96580dde4952f37cc017fef732bb40bfc73f28f25e3f64b4
FROM ${BASE_IMAGE}

WORKDIR /app

# Install system dependencies for Docling
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY gpu/requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared components
COPY shared/*.py /app/
COPY bounded_upload.py /app/

# Copy GPU processor
COPY gpu/processor.py /app/

# Create directories
RUN mkdir -p /models /output

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Run server
CMD ["python", "-m", "uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000"]
