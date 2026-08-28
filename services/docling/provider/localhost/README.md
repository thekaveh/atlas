# 5.3.1. Docling Localhost Provider

Run IBM Docling document processing natively on your host machine (any platform with Python).

## 1. Quick Start

### 1.1. Install Dependencies

```bash
cd services/docling/provider/localhost
uv sync
```

This installs all required dependencies (docling, fastapi, uvicorn, pydantic, etc.)

**For GPU acceleration (NVIDIA CUDA):**
```bash
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

**For Apple Silicon (MPS):**
```bash
uv pip install torch torchvision
```

### 1.2. Generate the Atlas Credential

From the repository root, start Atlas once so it generates and preserves
`DOCLING_API_TOKEN` in `.env` before the provider imports its settings:

```bash
# Terminal 1: return to the repository root after §1.1
cd ../../../..
./start.sh --doc-processor-source docling-localhost
```

Starting the provider first leaves the already-running process without the
generated token; restart it after Atlas creates `.env` if you did so.

### 1.3. Start the Server

```bash
# Terminal 2, opened at the repository root
cd services/docling/provider/localhost
uv run server.py
```

The server loads the repository `.env` at process import and starts on
`http://127.0.0.1:18159` by default (using `DOCLING_LOCALHOST_PORT` and
`DOCLING_API_TOKEN`).

**First run:** Downloads AI models (~500MB - DocLayNet + TableFormer). Please be patient (5-10 minutes).
**Subsequent runs:** Instant startup.

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

Set before running server:

```bash
export DOCLING_LOCALHOST_PORT=18159      # Server port (default: 18159)
export DOCLING_LOCALHOST_BIND_HOST=127.0.0.1
export DOCLING_API_TOKEN="$(sed -n 's/^DOCLING_API_TOKEN=//p' ../../../../.env)"
export DOCLING_AUTH_MODE=required
export DOCLING_MAX_FILE_SIZE=52428800
export DOCLING_UPLOAD_TIMEOUT_SECONDS=120
export DOCLING_INFERENCE_TIMEOUT_SECONDS=900
export DOCLING_DEVICE=cpu                # Device: cpu, cuda, mps
export DOCLING_OUTPUT_FORMAT=markdown    # Format: markdown, html, json, doctags
export DOCLING_USE_OCR=auto              # OCR: auto, always, never
export DOCLING_TABLE_MODE=accurate       # Table mode: accurate, fast
export DOCLING_ENABLE_FORMULAS=true      # Formula enrichment: true, false
export DOCLING_ENABLE_CODE_BLOCKS=true   # Code enrichment: true, false
export HF_TOKEN=your_token_here          # HuggingFace token (if needed)
```

The request's `use_ocr` and `table_mode` values override their environment
defaults. Device, formula enrichment, and code enrichment are applied through
Docling's pinned `PdfPipelineOptions` API. Unsupported output formats are
rejected during request validation; they are never silently returned as
Markdown.

### 2.2. Custom Port

```bash
export DOCLING_LOCALHOST_PORT=55021
uv run server.py
```

Or read from project .env:
```bash
# .env lives at the repo root, four levels up from this README
export DOCLING_LOCALHOST_PORT=$(grep '^DOCLING_LOCALHOST_PORT' ../../../../.env | cut -d'=' -f2)
uv run server.py
```

## 3. Supported Formats

### 3.1. Input Formats
- **Documents**: PDF, DOCX, DOC, PPTX, PPT, XLSX, HTML
- **Images**: PNG, JPG, JPEG, TIFF, TIF

### 3.2. Output Formats
- **markdown** - Clean markdown (default)
- **html** - Semantic HTML
- **json** - Structured JSON with metadata
- **doctags** - IBM Docling native format

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
- **Accurate Mode**: Uses TableFormer AI model (slow, high quality)
- **Fast Mode**: Rule-based extraction (10x faster, lower quality)

### 5.2. OCR Support
- **Auto**: Only uses OCR when needed (scanned PDFs, images)
- **Always**: Forces OCR on all documents
- **Never**: Disables OCR completely

### 5.3. Advanced Extraction
- Mathematical formulas (LaTeX format)
- Code blocks with syntax preservation
- Images and figures
- Document structure (headings, paragraphs, lists)

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

# Terminal 2, repository root: export the generated provider settings
export DOCLING_LOCALHOST_PORT="$(sed -n 's/^DOCLING_LOCALHOST_PORT=//p' .env)"
export DOCLING_API_TOKEN="$(sed -n 's/^DOCLING_API_TOKEN=//p' .env)"
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

## 7. Performance

### 7.1. CPU (Any Platform)
- Simple PDFs: ~2-5 seconds/page
- PDFs with tables: ~10-30 seconds/page
- Memory: ~2GB RAM

### 7.2. GPU (NVIDIA CUDA)
- Simple PDFs: ~1-2 seconds/page
- PDFs with tables: ~2-7 seconds/page (4.3x faster than CPU)
- Memory: ~2GB VRAM

### 7.3. Apple Silicon (MPS)
- Simple PDFs: ~1-3 seconds/page
- PDFs with tables: ~5-15 seconds/page
- Memory: ~2GB RAM

*Performance varies based on document complexity and table count*

## 8. Troubleshooting

### 8.1. Port Already in Use

```bash
# Use different port
export DOCLING_LOCALHOST_PORT=63090
uv run server.py
```

### 8.2. GPU Not Detected (NVIDIA)

```bash
# Install CUDA-enabled PyTorch
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# Verify CUDA
python -c "import torch; print(torch.cuda.is_available())"
```

### 8.3. Model Download Fails

```bash
# Set HuggingFace token if accessing gated models
export HF_TOKEN=your_token_here
uv run server.py

# Check disk space (need ~1GB free)
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

**Problem**: Document processing takes too long

**Solutions**:
- Use `table_mode=fast` for faster (less accurate) table extraction
- Reduce file size (compress images in PDF)
- Use GPU if available (4.3x speedup for tables)
- Disable OCR if not needed: `use_ocr=never`

## 9. Technical Details

### 9.1. Model Downloads

Models are downloaded on first run and cached in:
- **Linux/Mac**: `~/.cache/huggingface/`
- **Windows**: `%USERPROFILE%\.cache\huggingface\`

Downloaded models:
- **DocLayNet**: ~200MB (layout analysis)
- **TableFormer**: ~300MB (table structure recognition)

### 9.2. Device Selection

```python
# Auto-detected based on availability:
# 1. CUDA (NVIDIA GPU) if available
# 2. MPS (Apple Silicon) if available
# 3. CPU as fallback
```

Override with `DOCLING_DEVICE` environment variable.

### 9.3. Memory Requirements

- **Minimum**: 2GB RAM
- **Recommended**: 4GB RAM
- **GPU**: 2GB VRAM (for table extraction acceleration)

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
# Process multiple files
for file in *.pdf; do
  curl -X POST http://localhost:18159/v1/document/convert \
    -H "Authorization: Bearer ${DOCLING_API_TOKEN}" \
    -F "file=@$file" \
    -F "output_format=markdown" \
    > "${file%.pdf}.md"
done
```

## 11. References

- [Docling Documentation](https://docling-project.github.io/docling/)
- [Docling GitHub](https://github.com/DS4SD/docling)
- [DocLayNet Dataset](https://github.com/DS4SD/DocLayNet)
- [TableFormer Paper](https://arxiv.org/abs/2203.01017)
