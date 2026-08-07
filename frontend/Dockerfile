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
# Note: this installs the whole project's dependency set (fastembed, qdrant-client,
# sqlalchemy, etc. included) even though frontend/app.py only calls the backend over
# HTTP and never imports any of them -- accepted trade-off, single pyproject.toml
# shared with the backend, not worth a dependency-group split for a personal project.

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

# Application port (Gradio's own default; PORT env var overrides at runtime)

EXPOSE 7860

# Health check -- Gradio has no dedicated /health route, so this just confirms the
# root page responds.

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/')"

# Start Gradio

CMD ["python", "-m", "frontend.app"]
