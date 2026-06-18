# Substack Newsletters Search System — Technical Documentation

> Generated: 2026-06-17

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Configuration Management](#4-configuration-management)
5. [Data Models](#5-data-models)
6. [Data Pipeline](#6-data-pipeline)
7. [Vector Store (Qdrant)](#7-vector-store-qdrant)
8. [FastAPI Backend](#8-fastapi-backend)
9. [LLM Providers](#9-llm-providers)
10. [Gradio Frontend](#10-gradio-frontend)
11. [Infrastructure](#11-infrastructure)
12. [Testing](#12-testing)
13. [Deployment](#13-deployment)
14. [Makefile Reference](#14-makefile-reference)
15. [Environment Variables Reference](#15-environment-variables-reference)
16. [Dependencies](#16-dependencies)

---

## 1. Project Overview

The **Substack Newsletters Search System** is a production-grade Retrieval-Augmented Generation (RAG) application that ingests Substack newsletter articles and enables users to search or ask questions over the content using natural language.

### What it does

- **Ingests** RSS feeds from Substack newsletters into a PostgreSQL database (Supabase)
- **Embeds** article chunks as hybrid (dense + sparse) vectors into a Qdrant vector store
- **Searches** using semantic similarity and keyword retrieval via a FastAPI backend
- **Generates** LLM-powered answers with citations using multiple AI providers
- **Presents** results through a Gradio web UI with streaming support

### Technology Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI, Uvicorn |
| Frontend | Gradio |
| Relational DB | Supabase (PostgreSQL via SQLAlchemy) |
| Vector Store | Qdrant (hybrid dense + sparse) |
| Embeddings | Fastembed (BAAI/bge-base-en), BM25 |
| Orchestration | Prefect |
| LLM Providers | OpenRouter, OpenAI, Hugging Face |
| Observability | Opik AI (G-Eval) |
| Deployment | Google Cloud Run, Docker |
| Config | Pydantic Settings v2 |
| Package Manager | uv |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      USER INTERFACE                         │
│                  Gradio (port 7860)                         │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP (REST)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   FASTAPI BACKEND (port 8080)               │
│                                                             │
│  Routes: /health  /search/unique-titles  /search/ask       │
│                                                             │
│  ┌──────────────────┐    ┌─────────────────────────────┐   │
│  │  Search Service  │    │    Generation Service        │   │
│  │  (query_with_    │    │  (generate_answer /          │   │
│  │   filters)       │    │   get_streaming_function)    │   │
│  └────────┬─────────┘    └──────────────┬──────────────┘   │
│           │                             │                   │
│           ▼                             ▼                   │
│  ┌──────────────────┐    ┌─────────────────────────────┐   │
│  │  Qdrant Client   │    │    LLM Providers             │   │
│  │  (hybrid search) │    │  OpenRouter / OpenAI / HF    │   │
│  └──────────────────┘    └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   DATA PIPELINE (Prefect)                   │
│                                                             │
│  RSS Ingestion Flow          Embedding Ingestion Flow       │
│  ┌──────────────────┐        ┌──────────────────────┐      │
│  │ fetch_rss_entries│        │   ingest_qdrant       │      │
│  │ (task)           │        │   (task)              │      │
│  └────────┬─────────┘        └──────────┬───────────┘      │
│           │                             │                   │
│           ▼                             ▼                   │
│  ┌──────────────────┐        ┌──────────────────────┐      │
│  │ ingest_from_rss  │        │  AsyncQdrantVector    │      │
│  │ (task, batched)  │        │  Store               │      │
│  └────────┬─────────┘        └──────────┬───────────┘      │
│           │                             │                   │
│           ▼                             ▼                   │
│      Supabase DB               Qdrant Vector Store          │
└─────────────────────────────────────────────────────────────┘
```

### Request Flow — Q&A

1. User enters a question in Gradio
2. Gradio sends `POST /search/ask` or `/search/ask/stream` to FastAPI
3. FastAPI search service generates dense + sparse query vectors
4. Qdrant returns top-k chunks via hybrid RRF fusion
5. Generation service builds a prompt with retrieved context
6. LLM provider streams or returns the answer
7. Gradio renders the response (with streaming support)

### Request Flow — Article Search

1. User enters keywords in Gradio
2. Gradio sends `POST /search/unique-titles` to FastAPI
3. Search service performs hybrid vector search
4. Results are deduplicated by title
5. Gradio renders a formatted list of matching articles

---

## 3. Directory Structure

```
substack-newsletters-search-system/
│
├── src/                              # Application source
│   ├── config.py                     # Pydantic Settings (all env vars)
│   ├── configs/
│   │   └── feeds_rss.yaml            # RSS feed list (name, author, url)
│   │
│   ├── models/                       # Shared data models
│   │   ├── sql_models.py             # SQLAlchemy ORM (SubstackArticle)
│   │   ├── article_models.py         # Pydantic (FeedItem, ArticleItem)
│   │   └── vectorstore_models.py     # Pydantic (ArticleChunkPayload)
│   │
│   ├── api/                          # FastAPI application
│   │   ├── main.py                   # App factory, lifespan, middleware
│   │   ├── routes/
│   │   │   ├── health_routes.py      # GET /, /health, /ready
│   │   │   └── search_routes.py      # POST /search/*
│   │   ├── services/
│   │   │   ├── search_service.py     # Hybrid vector search logic
│   │   │   ├── generation_service.py # LLM orchestration
│   │   │   └── providers/
│   │   │       ├── openrouter_service.py
│   │   │       ├── openai_service.py
│   │   │       ├── huggingface_service.py
│   │   │       └── utils/
│   │   │           ├── prompts.py             # Prompt builder
│   │   │           └── evaluation_metrics.py  # G-Eval (OpenRouter)
│   │   ├── models/
│   │   │   ├── search_models.py      # SearchResult, AskRequest/Response
│   │   │   └── provider_models.py    # ModelConfig, MODEL_REGISTRY
│   │   ├── exceptions/               # Custom exception handlers
│   │   └── middleware/
│   │       └── logging_middleware.py # Request/response logging
│   │
│   ├── infrastructure/
│   │   ├── supabase/
│   │   │   ├── init_session.py       # SQLAlchemy engine + session factory
│   │   │   └── create_db.py          # Table creation script
│   │   └── qdrant/
│   │       ├── qdrant_vectorstore.py # AsyncQdrantVectorStore class
│   │       ├── create_collection.py  # Initialize Qdrant collection
│   │       ├── create_indexes.py     # HNSW + payload index creation
│   │       ├── delete_collection.py  # Collection cleanup
│   │       └── ingest_from_sql.py    # Direct SQL→Qdrant ingestion
│   │
│   ├── pipelines/
│   │   ├── flows/
│   │   │   ├── rss_ingestion_flow.py        # Prefect: RSS→Supabase
│   │   │   └── embeddings_ingestion_flow.py # Prefect: Supabase→Qdrant
│   │   └── tasks/
│   │       ├── fetch_rss.py         # Task: parse RSS feeds
│   │       ├── ingest_rss.py        # Task: batch insert to Supabase
│   │       └── ingest_embeddings.py # Task: embed and upsert to Qdrant
│   │
│   └── utils/
│       ├── text_splitter.py          # LangChain RecursiveCharacterTextSplitter
│       └── logging.py               # Loguru logger setup
│
├── frontend/
│   └── app.py                        # Gradio UI
│
├── tests/
│   ├── conftest.py                   # Fixtures: db_engine, db_session
│   ├── unit/
│   │   ├── test_fastapi.py           # API endpoint tests
│   │   └── test_fetch_rss_entries.py # RSS parsing tests
│   └── integration/
│       ├── test_db_connection.py     # Supabase connectivity
│       └── test_rss_pipeline.py      # End-to-end RSS pipeline
│
├── static/
│   └── app_diagram.png               # Architecture diagram
│
├── main.py                           # Alternate entry point
├── Makefile                          # Task automation
├── Dockerfile                        # FastAPI container image
├── pyproject.toml                    # Dependencies (uv/pip)
├── requirements.txt                  # Prefect deployment deps
├── prefect-cloud.yaml                # Prefect Cloud deployment config
├── prefect-local.yaml                # Prefect local deployment config
├── cloudbuild_fastapi.yaml           # Google Cloud Build config
├── deploy_fastapi.sh                 # Cloud Run deploy script
├── .env.example                      # Environment variable template
├── README.md                         # Project overview
├── PROJECTFLOW.md                    # Developer flow reference
└── INSTRUCTIONS.md                   # Setup instructions
```

---

## 4. Configuration Management

**File:** `src/config.py`

All configuration uses **Pydantic Settings v2** with environment variable support. Nested settings classes use `__` as the delimiter for env var mapping.

### Settings Classes

| Class | Env Prefix | Description |
|---|---|---|
| `SupabaseDBSettings` | `SUPABASE_DB__` | PostgreSQL connection params |
| `QdrantSettings` | `QDRANT__` | Vector store URL, API key, collection, model names |
| `RSSSettings` | `RSS__` | Feed configuration path |
| `TextSplitterSettings` | `TS__` | Chunk size and overlap |
| `JinaSettings` | `JINA__` | Jina embeddings API |
| `HuggingFaceSettings` | `HUGGING_FACE__` | HF inference API key |
| `OpenAISettings` | `OPENAI__` | OpenAI API key |
| `OpenRouterSettings` | `OPENROUTER__` | OpenRouter API key |
| `OpikObservabilitySettings` | `OPIK__` | Opik project and API key |

### Usage Pattern

```python
from src.config import settings

db_host = settings.supabase_db.host
qdrant_url = settings.qdrant.url
chunk_size = settings.text_splitter.chunk_size
```

### RSS Feed Configuration

**File:** `src/configs/feeds_rss.yaml`

```yaml
feeds:
  - name: AI Echoes
    author: Benito Martin
    url: https://aiechoes.substack.com/feed
  - name: The Neural Maze
    author: Miguel Otero Pedrido
    url: https://theneuralmaze.substack.com/feed
  - name: Decoding ML
    author: Paul Iusztin
    url: https://decodingml.substack.com/feed
```

---

## 5. Data Models

### 5.1 SQL Model — `SubstackArticle`

**File:** `src/models/sql_models.py`

PostgreSQL table for raw article storage.

| Column | Type | Constraints |
|---|---|---|
| `id` | Integer | Primary key, auto-increment |
| `uuid` | String | Unique, non-null |
| `feed_name` | String | Non-null |
| `feed_author` | String | Non-null |
| `article_authors` | String | Nullable |
| `title` | String | Non-null |
| `url` | String | Unique, non-null |
| `content` | Text | Non-null |
| `published_at` | DateTime | Nullable |
| `created_at` | DateTime | Default: `now()` |

### 5.2 Pydantic Models

**`FeedItem`** — RSS feed metadata:
- `name: str` — Feed display name
- `author: str` — Author name
- `url: str` — RSS feed URL

**`ArticleItem`** — Parsed article:
- `title: str`
- `content: str`
- `authors: list[str]`
- `published_at: datetime | None`

**`ArticleChunkPayload`** — Qdrant point payload:
- `chunk_id: int` — Chunk index within article
- `feed_name: str`
- `feed_author: str`
- `article_authors: str`
- `title: str`
- `url: str`
- `content: str`
- `published_at: datetime | None`

### 5.3 API Models

**`SearchResult`:**
- `score: float` — Relevance score
- `payload: ArticleChunkPayload`

**`AskRequest`:**
- `query: str`
- `feed_author: str | None`
- `feed_name: str | None`
- `title_keywords: list[str] | None`
- `model_config: ModelConfig`

**`AskResponse`:**
- `answer: str`
- `model_used: str`
- `sources: list[SearchResult]`

**`ModelConfig`:**
- `provider: str` — `"openrouter"` | `"openai"` | `"huggingface"`
- `model_id: str` — Model identifier
- `auto_route: bool` — Whether to auto-select model

---

## 6. Data Pipeline

### 6.1 RSS Ingestion Flow

**File:** `src/pipelines/flows/rss_ingestion_flow.py`

**Trigger:** `make ingest-rss-articles-flow`

```
rss_ingest_flow()
  │
  ├── Load feeds_rss.yaml
  │
  ├── [parallel] fetch_rss_entries(feed) per feed
  │     ├── HTTP GET RSS XML
  │     ├── Parse XML (feedparser)
  │     ├── Convert HTML → Markdown (markdownify + BeautifulSoup)
  │     ├── Skip paywalled articles ("read more" detection)
  │     └── Return list[ArticleItem]
  │
  └── [sequential] ingest_from_rss(articles, feed)
        ├── Batch articles (default batch_size=5)
        ├── Bulk INSERT to Supabase
        └── Rollback batch on error
```

**Paywall detection:** Checks for short content or "read more" anchor links that signal gated content.

**HTML to Markdown conversion:** Uses `markdownify` with BeautifulSoup cleanup to remove scripts, styles, and navigation elements before conversion.

### 6.2 Embedding Ingestion Flow

**File:** `src/pipelines/flows/embeddings_ingestion_flow.py`

**Trigger:** `make ingest-embeddings-flow` (or with `FROM_DATE=YYYY-MM-DD`)

```
qdrant_ingest_flow(from_date=None)
  │
  ├── Determine from_date:
  │     - CLI argument → use it directly
  │     - No argument → last successful Prefect flow run date
  │     - No prior run → ingest all articles
  │
  └── ingest_qdrant(from_date)
        │
        ├── Query Supabase: SELECT articles WHERE published_at >= from_date
        ├── Chunk each article (TextSplitter: 4000 chars, 200 overlap)
        ├── Generate dense vectors (BAAI/bge-base-en, 768-dim)
        ├── Generate sparse vectors (Qdrant/bm25)
        └── Batch upsert to Qdrant
```

**Incremental updates:** The flow uses Prefect's run history to determine the last successful run date, enabling delta ingestion rather than full re-indexing.

### 6.3 Text Splitting

**File:** `src/utils/text_splitter.py`

Wraps LangChain's `RecursiveCharacterTextSplitter`.

| Parameter | Default | Description |
|---|---|---|
| `chunk_size` | 4000 chars | ~600–800 words per chunk |
| `chunk_overlap` | 200 chars | Context preservation between chunks |
| `separators` | `["\n# ", "\n## ", "\n### ", "\n\n", "\n", ". ", " "]` | Split priority order |

The separator hierarchy respects Markdown heading structure before falling back to paragraph breaks, then sentence boundaries.

---

## 7. Vector Store (Qdrant)

**File:** `src/infrastructure/qdrant/qdrant_vectorstore.py`

### AsyncQdrantVectorStore

The central class managing all Qdrant operations. Initialized in FastAPI's lifespan and injected via dependency.

#### Collection Configuration

| Parameter | Value |
|---|---|
| Dense model | `BAAI/bge-base-en` |
| Dense dimensions | 768 |
| Sparse model | `Qdrant/bm25` |
| Quantization | INT8 scalar (memory efficient) |
| Search fusion | RRF (Reciprocal Rank Fusion) |

#### Key Methods

| Method | Description |
|---|---|
| `create_collection()` | Creates Qdrant collection with dense + sparse named vectors |
| `dense_vectors(texts)` | Batch encode texts to 768-dim dense vectors |
| `sparse_vectors(texts)` | Encode texts to BM25 sparse vectors |
| `ingest_from_sql(session, from_date)` | Full ingestion pipeline: SQL → chunk → embed → upsert |
| `search(query, filters, limit)` | Hybrid search with optional metadata filters |

#### Hybrid Search

Uses Qdrant's `FusionQuery` with RRF to combine:
- **Dense search**: Semantic similarity via cosine distance
- **Sparse search**: BM25 keyword matching

Filters supported: `feed_author`, `feed_name`, `title_keywords` (substring match on title field).

#### Index Strategy

1. **Bulk ingestion**: Load without indexes (fast upload)
2. **Post-load**: `make qdrant-create-index` creates HNSW + payload indexes
3. **HNSW**: Approximate nearest neighbor for dense vectors
4. **Payload indexes**: Enable fast metadata filtering on `feed_name`, `feed_author`, `title`

---

## 8. FastAPI Backend

**File:** `src/api/main.py`

**Port:** 8080 (configurable)

### Application Lifecycle

The app uses FastAPI's `lifespan` context manager to:
- Initialize `AsyncQdrantVectorStore` on startup
- Store it in `app.state.vector_store`
- Close the Qdrant client on shutdown

### Routes

#### Health Routes (`src/api/routes/health_routes.py`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Root — returns service name |
| `GET` | `/health` | Liveness check — always 200 |
| `GET` | `/ready` | Readiness — checks Qdrant connectivity |

#### Search Routes (`src/api/routes/search_routes.py`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/search/unique-titles` | Hybrid search, deduplicated article titles |
| `POST` | `/search/ask` | Non-streaming Q&A with LLM |
| `POST` | `/search/ask/stream` | Streaming Q&A with LLM (SSE) |

### Search Service

**File:** `src/api/services/search_service.py`

#### `query_with_filters(query, filters, vector_store, limit)`

1. Generates dense embedding for the query
2. Generates sparse BM25 embedding for the query
3. Builds Qdrant filter from provided metadata fields
4. Executes hybrid fusion query
5. Returns `list[SearchResult]` ordered by relevance score

#### `query_unique_titles(query, filters, vector_store, limit)`

Same as above, then deduplicates results by `title` field, keeping the highest-scoring chunk per article.

### Generation Service

**File:** `src/api/services/generation_service.py`

#### `generate_answer(query, results, model_config)`

1. Calls `build_research_prompt(query, results)` to construct the prompt
2. Routes to the appropriate provider (`openrouter`, `openai`, or `huggingface`)
3. Returns `(answer_text, model_used_str)`
4. Optionally tracks with Opik

#### `get_streaming_function(query, results, model_config)`

Returns an async generator that yields text chunks. The stream protocol:

| Prefix | Meaning |
|---|---|
| `__model_used__:<model>` | First chunk — identifies the model |
| `__error__` | Error occurred |
| `__truncated__` | Response was cut at token limit |
| *(plain text)* | Answer content chunk |

### Prompt Construction

**File:** `src/api/services/providers/utils/prompts.py`

`build_research_prompt(query, results)` builds a structured prompt that:
- States the user question
- Injects all retrieved chunks with source attribution (title, url, author)
- Instructs the LLM to respond in Markdown with inline citations
- Enforces a token limit in the instructions
- Requests a "Sources" section at the end

### Exception Handlers

Custom handlers for:
- `RequestValidationError` — 422, formatted field errors
- `QdrantException` — 503, vector store unavailable
- Generic `Exception` — 500, unexpected errors

All errors log via Loguru and return structured JSON.

### Middleware

**File:** `src/api/middleware/logging_middleware.py`

Logs each request: method, path, status code, and duration (ms). Uses Loguru structured logging.

---

## 9. LLM Providers

**Directory:** `src/api/services/providers/`

All providers implement two functions:
- `generate_<provider>(prompt, model_id) → str`
- `stream_<provider>(prompt, model_id) → AsyncGenerator[str, None]`

### Provider Registry

**File:** `src/api/models/provider_models.py`

`MODEL_REGISTRY` maps provider names to available `ModelConfig` objects:

```python
MODEL_REGISTRY = {
    "openrouter": [
        ModelConfig(model_id="mistralai/mistral-7b-instruct:free", ...),
        ModelConfig(model_id="google/gemma-3-12b-it:free", ...),
        ...
    ],
    "openai": [
        ModelConfig(model_id="gpt-4o-mini", ...),
        ...
    ],
    "huggingface": [
        ModelConfig(model_id="mistralai/Mistral-7B-Instruct-v0.3", ...),
        ...
    ],
}
```

### OpenRouter (`openrouter_service.py`)

- Primary provider; uses free-tier models via OpenRouter API
- Compatible with OpenAI client (`base_url="https://openrouter.ai/api/v1"`)
- **Evaluation**: After generation, optionally runs G-Eval via `evaluation_metrics.py`
  - Evaluates: coherence, relevance, fluency, factual correctness
  - Uses the LLM itself as a judge (self-evaluation pattern)

### OpenAI (`openai_service.py`)

- Standard OpenAI API client
- Supports both sync and streaming completions

### Hugging Face (`huggingface_service.py`)

- Uses HF Inference API (hosted models)
- Streams via HF's async streaming API

---

## 10. Gradio Frontend

**File:** `frontend/app.py`

**Port:** 7860 (default Gradio port)

**Connects to:** FastAPI backend at `BACKEND_URL` (env var)

### UI Layout

```
┌──────────────────────────────────────────────────┐
│  Query Input (3-line textbox)                    │
│                                                  │
│  Filters:                                        │
│    Feed Author │ Feed Name │ Title Keywords       │
│                                                  │
│  Mode:                                           │
│    [ Search Articles ]  [ Ask AI ]               │
│                                                  │
│  AI Options (when Ask AI selected):              │
│    Provider: [OpenRouter ▼]                      │
│    Model:    [Auto-select ▼]                     │
│    [ ] Enable Streaming                          │
│                                                  │
│  Output Area                                     │
└──────────────────────────────────────────────────┘
```

### Key Functions

| Function | Description |
|---|---|
| `fetch_unique_titles(query, filters)` | `POST /search/unique-titles` → formatted HTML list |
| `call_ai(query, filters, model_config)` | `POST /search/ask` or `/stream` |
| `handle_search_articles()` | Renders article results as clickable HTML |
| `handle_ai_question_streaming()` | Yields answer chunks to Gradio output |
| `handle_ai_question_non_streaming()` | Returns complete answer at once |
| `update_model_choices(provider)` | Populates model dropdown based on provider selection |

### Streaming Protocol

The frontend consumes the SSE stream from FastAPI, parsing line prefixes:
- Accumulates plain text chunks into the output display
- Extracts `__model_used__` to display which model responded
- Handles `__error__` and `__truncated__` as special cases with appropriate UI messages

---

## 11. Infrastructure

### Supabase (PostgreSQL)

**Files:** `src/infrastructure/supabase/`

#### `init_session.py`

```python
def init_engine() -> Engine:
    """Creates SQLAlchemy engine with connection pooling."""

def init_session(engine: Engine) -> Session:
    """Creates a new database session."""
```

- URL-encodes the password to handle special characters
- Pool size and overflow are tunable via env vars

#### `create_db.py`

Uses SQLAlchemy's `inspect()` to check if the `substack_articles` table exists before attempting to create it. Run via `make supabase-create`.

### Qdrant Setup Scripts

| Script | Makefile Command | Purpose |
|---|---|---|
| `create_collection.py` | `make qdrant-create-collection` | Initialize collection with vector configs |
| `create_indexes.py` | `make qdrant-create-index` | Create HNSW + payload indexes post-bulk-load |
| `delete_collection.py` | `make qdrant-delete-collection` | Drop and recreate collection |
| `ingest_from_sql.py` | `make qdrant-ingest-from-sql` | Direct SQL→Qdrant ingestion (bypasses flow) |

---

## 12. Testing

**Directory:** `tests/`

### Test Configuration (`conftest.py`)

Provides session-scoped fixtures:
- `db_engine` — creates a SQLAlchemy engine pointing to the test database
- `db_session` — creates a transaction-wrapped session (auto-rollback after each test)

### Unit Tests

| File | Tests |
|---|---|
| `tests/unit/test_fastapi.py` | Health endpoints, route structure, response schemas |
| `tests/unit/test_fetch_rss_entries.py` | RSS parsing edge cases: paywalled content, empty feeds, malformed XML |

### Integration Tests

| File | Tests |
|---|---|
| `tests/integration/test_db_connection.py` | Live Supabase connection, table existence |
| `tests/integration/test_rss_pipeline.py` | End-to-end: fetch RSS → parse → insert → query |

### Running Tests

```bash
make all-tests        # Run all tests
uv run pytest tests/unit/
uv run pytest tests/integration/
```

---

## 13. Deployment

### Local Development

```bash
# 1. Copy and fill env vars
cp .env.example .env

# 2. Install dependencies
uv pip install -e .

# 3. Create database table
make supabase-create

# 4. Create Qdrant collection
make qdrant-create-collection

# 5. Ingest articles
make ingest-rss-articles-flow
make ingest-embeddings-flow
make qdrant-create-index

# 6. Start services (two terminals)
make run-api      # Terminal 1
make run-gradio   # Terminal 2

# 7. Open UI
# http://127.0.0.1:7860
```

### Docker (FastAPI Only)

```bash
docker build -t substack-search .
docker run -p 8080:8080 --env-file .env substack-search
```

**Dockerfile** uses a multi-stage build:
1. Install dependencies with uv
2. Copy source code
3. Expose port 8080
4. Run `uvicorn src.api.main:app`

### Google Cloud Run

**Script:** `deploy_fastapi.sh`  
**Build config:** `cloudbuild_fastapi.yaml`

```bash
# Build and deploy via Cloud Build
gcloud builds submit --config cloudbuild_fastapi.yaml
```

The Cloud Build config:
1. Builds the Docker image
2. Pushes to Google Container Registry
3. Deploys to Cloud Run with env vars from Secret Manager

### Prefect Deployment

**Cloud deployment:**
```bash
make deploy-cloud-flows
```
Uses `prefect-cloud.yaml` which defines both flows (RSS ingestion + embedding ingestion) with their schedules and parameters.

**Local deployment:**
```bash
make deploy-local-flows
```
Uses `prefect-local.yaml` for a local Prefect server.

---

## 14. Makefile Reference

### Database

| Command | Description |
|---|---|
| `make supabase-create` | Create `substack_articles` table in Supabase |
| `make supabase-delete` | Drop the table |

### Vector Store

| Command | Description |
|---|---|
| `make qdrant-create-collection` | Initialize Qdrant collection |
| `make qdrant-create-index` | Create HNSW and payload indexes |
| `make qdrant-delete-collection` | Delete and recreate collection |
| `make qdrant-ingest-from-sql` | Direct ingestion from Supabase to Qdrant |

### Pipeline

| Command | Description |
|---|---|
| `make ingest-rss-articles-flow` | Run RSS ingestion Prefect flow |
| `make ingest-embeddings-flow` | Run embedding ingestion Prefect flow |
| `make ingest-embeddings-flow FROM_DATE=2025-01-01` | Incremental ingest from a date |
| `make deploy-cloud-flows` | Deploy flows to Prefect Cloud |
| `make deploy-local-flows` | Deploy flows to local Prefect server |

### Services

| Command | Description |
|---|---|
| `make run-api` | Start FastAPI on port 8080 |
| `make run-gradio` | Start Gradio on port 7860 |

### Code Quality

| Command | Description |
|---|---|
| `make all-check` | Run ruff lint + format check + mypy |
| `make all-fix` | Auto-fix lint + format issues |
| `make all-tests` | Run full test suite |
| `make clean` | Remove `__pycache__`, `.mypy_cache`, etc. |

---

## 15. Environment Variables Reference

Copy `.env.example` to `.env` and fill in all values.

### Supabase (PostgreSQL)

| Variable | Description |
|---|---|
| `SUPABASE_DB__HOST` | Database host |
| `SUPABASE_DB__USER` | Database user |
| `SUPABASE_DB__PASSWORD` | Database password |
| `SUPABASE_DB__PORT` | Database port (default: 5432) |
| `SUPABASE_DB__NAME` | Database name |

### Qdrant

| Variable | Description |
|---|---|
| `QDRANT__URL` | Qdrant cluster URL |
| `QDRANT__API_KEY` | Qdrant API key |
| `QDRANT__COLLECTION_NAME` | Collection name |
| `QDRANT__DENSE_MODEL` | Dense embedding model (default: BAAI/bge-base-en) |
| `QDRANT__SPARSE_MODEL` | Sparse embedding model (default: Qdrant/bm25) |

### Text Splitting

| Variable | Description |
|---|---|
| `TS__CHUNK_SIZE` | Characters per chunk (default: 4000) |
| `TS__CHUNK_OVERLAP` | Overlap between chunks (default: 200) |

### LLM Providers

| Variable | Description |
|---|---|
| `OPENROUTER__API_KEY` | OpenRouter API key |
| `OPENAI__API_KEY` | OpenAI API key |
| `HUGGING_FACE__API_KEY` | Hugging Face API token |

### Observability

| Variable | Description |
|---|---|
| `OPIK__API_KEY` | Opik AI API key |
| `OPIK__PROJECT_NAME` | Opik project name |

### Application

| Variable | Description |
|---|---|
| `BACKEND_URL` | FastAPI URL for Gradio (default: http://localhost:8080) |
| `ALLOWED_ORIGINS` | CORS allowed origins |

### Prefect

| Variable | Description |
|---|---|
| `PREFECT__API_KEY` | Prefect Cloud API key |
| `PREFECT__WORKSPACE` | Prefect workspace slug |
| `PREFECT__API_URL` | Prefect API URL |

---

## 16. Dependencies

### Core Framework

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | >=0.135 | REST API framework |
| `uvicorn` | >=0.42 | ASGI server |
| `pydantic` | >=2.12 | Data validation and settings |
| `sqlalchemy` | >=2.0 | ORM and database toolkit |

### Data & Storage

| Package | Version | Purpose |
|---|---|---|
| `supabase` | >=2.28 | Supabase client (PostgreSQL) |
| `qdrant-client` | >=1.17 | Qdrant vector store client |
| `prefect` | >=3.6 | Workflow orchestration |

### ML / Embeddings

| Package | Version | Purpose |
|---|---|---|
| `fastembed` | >=0.8 | ONNX-accelerated dense embeddings |
| `langchain` | >=1.2 | Text splitting utilities |
| `openai` | >=2.29 | OpenAI and OpenRouter client |

### Frontend

| Package | Version | Purpose |
|---|---|---|
| `gradio` | >=6.9 | Web UI framework |

### Data Processing

| Package | Version | Purpose |
|---|---|---|
| `requests` | >=2.32 | HTTP client for RSS fetching |
| `beautifulsoup4` | >=4.14 | HTML parsing |
| `lxml` | >=6.0 | XML/HTML parser backend |
| `markdownify` | >=1.2 | HTML to Markdown conversion |
| `pyyaml` | — | YAML config parsing |

### Observability & Logging

| Package | Version | Purpose |
|---|---|---|
| `loguru` | >=0.7 | Structured logging |
| `opik` | >=1.10 | LLM evaluation and observability |

### Dev / Quality

| Package | Version | Purpose |
|---|---|---|
| `pytest` | >=9.0 | Testing framework |
| `ruff` | — | Linting and formatting |
| `mypy` | — | Static type checking |
| `pre-commit` | — | Git hooks for quality gates |

---

*End of documentation.*
