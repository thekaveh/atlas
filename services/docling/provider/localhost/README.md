# Docling Document Processor - Localhost Mode

Run IBM Docling document processing natively on your host machine (any platform with Python).

## Quick Start

### 1. Install Dependencies

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

### 2. Start the Server

```bash
uv run server.py
```

The server will start on `http://0.0.0.0:18159` by default (reads `DOCLING_LOCALHOST_PORT` from environment).

**First run:** Downloads AI models (~500MB - DocLayNet + TableFormer). Please be patient (5-10 minutes).
**Subsequent runs:** Instant startup.

### 3. Test the API

```bash
curl -X POST http://localhost:18159/v1/document/convert \
  -F "file=@document.pdf" \
  -F "output_format=markdown" \
  -F "table_mode=accurate"
```

## Configuration

### Environment Variables

Set before running server:

```bash
export DOCLING_LOCALHOST_PORT=18159      # Server port (default: 18159)
export DOCLING_DEVICE=cpu                # Device: cpu, cuda, mps
export DOCLING_OUTPUT_FORMAT=markdown    # Format: markdown, html, json, doctags
export DOCLING_TABLE_MODE=accurate       # Table mode: accurate, fast
export HF_TOKEN=your_token_here          # HuggingFace token (if needed)
```

### Custom Port

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

## Supported Formats

### Input Formats
- **Documents**: PDF, DOCX, DOC, PPTX, PPT, XLSX, HTML
- **Images**: PNG, JPG, JPEG, TIFF, TIF

### Output Formats
- **markdown** - Clean markdown (default)
- **html** - Semantic HTML
- **json** - Structured JSON with metadata
- **doctags** - IBM Docling native format

## API Examples

### Basic Conversion

```bash
curl -X POST http://localhost:18159/v1/document/convert \
  -F "file=@report.pdf" \
  -F "output_format=markdown"
```

### With OCR and Table Extraction

```bash
curl -X POST http://localhost:18159/v1/document/convert \
  -F "file=@scanned.pdf" \
  -F "use_ocr=always" \
  -F "table_mode=accurate"
```

### RAG Chunking

```bash
curl -X POST http://localhost:18159/v1/document/convert \
  -F "file=@document.docx" \
  -F "enable_chunking=true" \
  -F "chunk_size=512" \
  -F "chunk_overlap=50"
```

## Features

### Table Extraction
- **Accurate Mode**: Uses TableFormer AI model (slow, high quality)
- **Fast Mode**: Rule-based extraction (10x faster, lower quality)

### OCR Support
- **Auto**: Only uses OCR when needed (scanned PDFs, images)
- **Always**: Forces OCR on all documents
- **Never**: Disables OCR completely

### Advanced Extraction
- Mathematical formulas (LaTeX format)
- Code blocks with syntax preservation
- Images and figures
- Document structure (headings, paragraphs, lists)

## Integration with Atlas

### Method 1: Localhost Mode (Recommended)

```bash
# Terminal 1: Start doc processor
cd services/docling/provider/localhost
uv run server.py

# Terminal 2: Start stack
./start.sh --doc-processor-source docling-localhost
```

### Method 2: With Custom Base Port

```bash
# Terminal 1: Export port from .env (repo root is four levels up)
export DOCLING_LOCALHOST_PORT=$(grep '^DOCLING_LOCALHOST_PORT' ../../../../.env | cut -d'=' -f2)
uv run server.py

# Terminal 2: Start stack with custom port
./start.sh --base-port 55000 --doc-processor-source docling-localhost
```

### Method 3: Permanent Configuration

Edit `.env` file:
```bash
DOC_PROCESSOR_SOURCE=docling-localhost
```

Then start stack:
```bash
./start.sh
```

## Performance

### CPU (Any Platform)
- Simple PDFs: ~2-5 seconds/page
- PDFs with tables: ~10-30 seconds/page
- Memory: ~2GB RAM

### GPU (NVIDIA CUDA)
- Simple PDFs: ~1-2 seconds/page
- PDFs with tables: ~2-7 seconds/page (4.3x faster than CPU)
- Memory: ~2GB VRAM

### Apple Silicon (MPS)
- Simple PDFs: ~1-3 seconds/page
- PDFs with tables: ~5-15 seconds/page
- Memory: ~2GB RAM

*Performance varies based on document complexity and table count*

## Troubleshooting

### Port Already in Use

```bash
# Use different port
export DOCLING_LOCALHOST_PORT=63090
uv run server.py
```

### GPU Not Detected (NVIDIA)

```bash
# Install CUDA-enabled PyTorch
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# Verify CUDA
python -c "import torch; print(torch.cuda.is_available())"
```

### Model Download Fails

```bash
# Set HuggingFace token if accessing gated models
export HF_TOKEN=your_token_here
uv run server.py

# Check disk space (need ~1GB free)
df -h
```

### Import Errors

```bash
# Reinstall dependencies
uv sync --reinstall

# Or use fresh environment
rm -rf .venv
uv sync
```

### Slow Processing

**Problem**: Document processing takes too long

**Solutions**:
- Use `table_mode=fast` for faster (less accurate) table extraction
- Reduce file size (compress images in PDF)
- Use GPU if available (4.3x speedup for tables)
- Disable OCR if not needed: `use_ocr=never`

## Technical Details

### Model Downloads

Models are downloaded on first run and cached in:
- **Linux/Mac**: `~/.cache/huggingface/`
- **Windows**: `%USERPROFILE%\.cache\huggingface\`

Downloaded models:
- **DocLayNet**: ~200MB (layout analysis)
- **TableFormer**: ~300MB (table structure recognition)

### Device Selection

```python
# Auto-detected based on availability:
# 1. CUDA (NVIDIA GPU) if available
# 2. MPS (Apple Silicon) if available
# 3. CPU as fallback
```

Override with `DOCLING_DEVICE` environment variable.

### Memory Requirements

- **Minimum**: 2GB RAM
- **Recommended**: 4GB RAM
- **GPU**: 2GB VRAM (for table extraction acceleration)

## Advanced Usage

### Python Integration

```python
import requests

with open("document.pdf", "rb") as f:
    response = requests.post(
        "http://localhost:18159/v1/document/convert",
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

### Batch Processing

```bash
# Process multiple files
for file in *.pdf; do
  curl -X POST http://localhost:18159/v1/document/convert \
    -F "file=@$file" \
    -F "output_format=markdown" \
    > "${file%.pdf}.md"
done
```

## References

- [Docling Documentation](https://docling-project.github.io/docling/)
- [Docling GitHub](https://github.com/DS4SD/docling)
- [DocLayNet Dataset](https://github.com/DS4SD/DocLayNet)
- [TableFormer Paper](https://arxiv.org/abs/2203.01017)
