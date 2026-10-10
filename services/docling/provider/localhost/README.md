# 5.3.1. Docling Localhost Provider

Run the Atlas Docling provider natively on the host. Atlas containers reach it
through `host.docker.internal` when `DOC_PROCESSOR_SOURCE=docling-localhost`.
It needs Python 3.10 or later and [uv](https://docs.astral.sh/uv/).

## 1. Quick Start

### 1.1. Install Dependencies

```bash
cd services/docling/provider/localhost
uv sync
```

This installs the locked dependencies, including `docling==2.102.1` and
`torch==2.13.0`. The PyPI torch wheel supports CUDA on Linux x86_64 and MPS on
Apple Silicon, so no separate torch install is needed. Do not install another
torch with `uv pip install`: `uv run` restores the locked version.

### 1.2. Generate the Atlas Credential

From the repository root, start Atlas once so it generates and preserves
`DOCLING_API_TOKEN` in `.env` before the provider imports its settings:

```bash
# Terminal 1: return to the repository root after §1.1
cd ../../../..
./start.sh --doc-processor-source docling-localhost
```

The provider reads `.env` once, when it starts. If you started it before Atlas
created the token, restart it.

### 1.3. Start the Server

```bash
# Terminal 2, opened at the repository root
cd services/docling/provider/localhost
uv run server.py
```

The server loads the repository `.env` (four levels up), then listens on
`http://127.0.0.1:18159` by default (`DOCLING_LOCALHOST_BIND_HOST` and
`DOCLING_LOCALHOST_PORT`). Variables exported in the shell override `.env`.

The first conversion downloads Docling's layout and table models from Hugging
Face, so it takes longer. Later runs reuse the cache.

### 1.4. Test the API

```bash
# Terminal 3, opened at the repository root while Atlas and the provider run
export DOCLING_API_TOKEN="$(sed -n 's/^DOCLING_API_TOKEN=//p' .env)"
curl -X POST http://localhost:18159/v1/document/convert \
  -H "Authorization: Bearer ${DOCLING_API_TOKEN}" \
  -F "file=@document.pdf" \
  -F "output_format=markdown" \
  -F "table_mode=accurate"
```

## 2. Configuration

### 2.1. Environment Variables

The server reads these from the shell or from the repository `.env`:

```bash
export DOCLING_LOCALHOST_PORT=18159      # Server port (default: 18159)
export DOCLING_LOCALHOST_BIND_HOST=127.0.0.1  # Listen address (see below)
export DOCLING_API_TOKEN="$(sed -n 's/^DOCLING_API_TOKEN=//p' ../../../../.env)"
export DOCLING_AUTH_MODE=required
export DOCLING_MAX_FILE_SIZE=52428800
export DOCLING_UPLOAD_TIMEOUT_SECONDS=120
export DOCLING_INFERENCE_TIMEOUT_SECONDS=900
export DOCLING_DEVICE=cpu                # Device: cpu (default), cuda, mps
export DOCLING_OUTPUT_FORMAT=markdown    # Format: markdown, html, json, doctags
export DOCLING_USE_OCR=auto              # OCR: auto, always, never
export DOCLING_TABLE_MODE=accurate       # Table mode: accurate, fast
export DOCLING_ENABLE_FORMULAS=true      # Formula enrichment: true, false
export DOCLING_ENABLE_CODE_BLOCKS=true   # Code enrichment: true, false
export HF_TOKEN=your_token_here          # Hugging Face token (if needed)
```

On Linux, containers reach the host through the Docker bridge, not loopback.
Bind the bridge IP, or `0.0.0.0` with `DOCLING_AUTH_MODE=required`.

The request's `use_ocr` and `table_mode` values override their environment
defaults. Device, formula enrichment and code enrichment are applied through
Docling's pinned `PdfPipelineOptions` API. Request validation rejects an
unsupported output format; the server never returns Markdown in its place.

### 2.2. Custom Port

```bash
export DOCLING_LOCALHOST_PORT=55021
uv run server.py
```

Set the same `DOCLING_LOCALHOST_PORT` in the repository `.env`, so Atlas
containers use that port.

## 3. Supported Formats

### 3.1. Input Formats

The provider uses Docling's default converter: PDF, DOCX, PPTX, XLSX, HTML and
images (PNG, JPEG, TIFF). Docling does not convert legacy Office files (`.doc`,
`.xls`, `.ppt`). The Atlas backend sends those to Tika; see the
[Document Processor README](../../../doc-processor/README.md).

### 3.2. Output Formats
- **markdown** - Markdown (default)
- **html** - HTML
- **json** - Docling document as JSON
- **doctags** - Docling's native DocTags format

## 4. API Examples

The examples below assume `DOCLING_API_TOKEN` was exported from the generated
repository `.env` as shown in §1.4.

### 4.1. Basic Conversion

```bash
curl -X POST http://localhost:18159/v1/document/convert \
  -H "Authorization: Bearer ${DOCLING_API_TOKEN}" \
  -F "file=@report.pdf" \
  -F "output_format=markdown"
```

### 4.2. With OCR and Table Extraction

```bash
curl -X POST http://localhost:18159/v1/document/convert \
  -H "Authorization: Bearer ${DOCLING_API_TOKEN}" \
  -F "file=@scanned.pdf" \
  -F "use_ocr=always" \
  -F "table_mode=accurate"
```

### 4.3. RAG Chunking

```bash
curl -X POST http://localhost:18159/v1/document/convert \
  -H "Authorization: Bearer ${DOCLING_API_TOKEN}" \
  -F "file=@document.docx" \
  -F "enable_chunking=true" \
  -F "chunk_size=512" \
  -F "chunk_overlap=50"
```

## 5. Features

### 5.1. Table Extraction
- **accurate**: the accurate TableFormer model (default).
- **fast**: the fast TableFormer model; quicker, with lower table quality.

### 5.2. OCR Support
- **auto**: OCR only where the document needs it, for example scanned pages.
- **always**: full-page OCR on every page.
- **never**: no OCR.

### 5.3. Advanced Extraction
- Mathematical formulas (`DOCLING_ENABLE_FORMULAS`)
- Code blocks (`DOCLING_ENABLE_CODE_BLOCKS`)
- Document structure: headings, paragraphs, lists and tables

## 6. Integration with Atlas

### 6.1. Method 1: Localhost Mode (Recommended)

```bash
# Terminal 1, repository root: generate/preserve .env credentials and run Atlas
./start.sh --doc-processor-source docling-localhost

# Terminal 2, after Atlas has generated .env: start the provider
cd services/docling/provider/localhost
uv run server.py
```

Atlas remains in the foreground in Terminal 1; use the separate Terminal 2 for
the native provider.

### 6.2. Method 2: With Custom Base Port

```bash
# Terminal 1, repository root: start Atlas first
./start.sh --base-port 55000 --doc-processor-source docling-localhost

# Terminal 2, repository root: the provider reads the port and token from .env
cd services/docling/provider/localhost
uv run server.py
```

### 6.3. Method 3: Permanent Configuration

Edit `.env` file:
```bash
DOC_PROCESSOR_SOURCE=docling-localhost
```

Then start stack:
```bash
# Terminal 1, repository root
./start.sh

# Terminal 2, after Atlas has generated/preserved the credential
cd services/docling/provider/localhost
uv run server.py
```

For long-lived use, run the provider under a service manager (systemd or
launchd) with restart-on-failure. After a conversion timeout the provider
exits with status 70, and a bare `uv run server.py` stays stopped.

## 7. Performance

Conversion time and memory depend on the device, the page count, tables, OCR
and `table_mode`. Atlas publishes no per-page figures. Benchmark
representative documents on the host before capacity planning.

## 8. Troubleshooting

### 8.1. Port Already in Use

```bash
# Use a different port, then set the same value in the repository .env
export DOCLING_LOCALHOST_PORT=18160
uv run server.py
```

### 8.2. GPU Not Used

`DOCLING_DEVICE` defaults to `cpu`; the server does not detect a GPU. Set
`DOCLING_DEVICE=cuda` or `DOCLING_DEVICE=mps`, then check that torch can see
the device:

```bash
uv run python -c "import torch; print(torch.cuda.is_available(), torch.backends.mps.is_available())"
```

### 8.3. Model Download Fails

```bash
# Set a Hugging Face token if the download needs one
export HF_TOKEN=your_token_here
uv run server.py

# Check free disk space for the Hugging Face cache
df -h
```

### 8.4. Import Errors

```bash
# Reinstall dependencies
uv sync --reinstall

# Or use fresh environment
rm -rf .venv
uv sync
```

### 8.5. Slow Processing

- Use `table_mode=fast` for faster, less accurate table extraction.
- Use `use_ocr=never` when the documents have a text layer.
- Set `DOCLING_DEVICE` to an available GPU device (§8.2).

## 9. Technical Details

### 9.1. Model Downloads

Docling downloads its layout and table models on first use and caches them in
the Hugging Face cache (`~/.cache/huggingface/` by default).

### 9.2. Device Selection

The device comes from `DOCLING_DEVICE` only: `cpu` (default), `cuda` or
`mps`. An invalid device makes `/health` report `unavailable`.

## 10. Advanced Usage

### 10.1. Python Integration

```python
import requests
import os

with open("document.pdf", "rb") as f:
    response = requests.post(
        "http://localhost:18159/v1/document/convert",
        headers={"Authorization": f"Bearer {os.environ['DOCLING_API_TOKEN']}"},
        files={"file": f},
        data={
            "output_format": "markdown",
            "table_mode": "accurate",
            "enable_chunking": True,
            "chunk_size": 512
        }
    )

result = response.json()
print(result["content"])
print(f"Processed {result['metadata']['pages']} pages")
print(f"Found {result['metadata']['tables']} tables")
```

### 10.2. Batch Processing

```bash
# Process multiple files; the response is JSON with the text in "content"
for file in *.pdf; do
  curl -X POST http://localhost:18159/v1/document/convert \
    -H "Authorization: Bearer ${DOCLING_API_TOKEN}" \
    -F "file=@$file" \
    -F "output_format=markdown" \
    > "${file%.pdf}.json"
done
```

## 11. References

- [Docling Documentation](https://docling-project.github.io/docling/)
- [Docling GitHub](https://github.com/docling-project/docling)
- [TableFormer Paper](https://arxiv.org/abs/2203.01017)
