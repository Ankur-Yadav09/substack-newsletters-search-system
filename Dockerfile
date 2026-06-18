# ---------- Builder Stage ----------

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# UV optimizations

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never

# Copy dependency files first for Docker layer caching

COPY pyproject.toml uv.lock ./

# Install dependencies

RUN uv sync --locked --no-install-project --no-dev

# Copy application source

COPY . .

# Install project into virtual environment

RUN uv sync --locked --no-dev

# ---------- Runtime Stage ----------

FROM python:3.12-slim-bookworm

WORKDIR /app

# Copy application and virtual environment

COPY --from=builder /app /app

# Environment variables

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app

# FastEmbed / HuggingFace cache locations

ENV HF_HOME=/tmp/huggingface
ENV FASTEMBED_CACHE=/tmp/fastembed_cache

# Create cache directories

RUN mkdir -p $HF_HOME $FASTEMBED_CACHE

# Application port

EXPOSE 8080

# Health check (requires /health endpoint)

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"
    
# Start FastAPI

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
