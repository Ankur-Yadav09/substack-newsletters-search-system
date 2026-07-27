# CLAUDE.md

Reference notes for AI assistants (and humans) working in this repository. This reflects the
**actual state of the code as of 2026-07-27**, verified by reading the source directly — not just
the aspirational README/DOCUMENTATION.md, which describe some files/infra that don't currently
exist in this checkout (see "Known Gaps" below).

## What this project is

A RAG (Retrieval-Augmented Generation) system that ingests Substack newsletter RSS feeds,
stores articles in Postgres (Supabase), embeds chunks into Qdrant (hybrid dense+sparse vectors),
and serves search/Q&A over a FastAPI backend with a Gradio frontend. This is a personal
implementation of Benito Martin's "Substack Articles Search Engine" course
(see README.md / INSTRUCTIONS.md / PROJECTFLOW.md for the course narrative), built incrementally
via `uv`, Prefect, FastAPI, and Docker.

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn, port 8080 |
| Frontend | Gradio, port 7860 |
| Relational DB | Supabase Postgres via SQLAlchemy (sync) |
| Vector DB | Qdrant, hybrid dense (Fastembed `BAAI/bge-base-en`, 768-dim) + sparse (`Qdrant/bm25`) |
| Orchestration | Prefect 3.x (flows/tasks), run locally — no deployment yaml checked in yet |
| LLM providers | OpenRouter (primary/free), OpenAI, Hugging Face — pluggable via `MODEL_REGISTRY`, selected via the `LLMProvider` enum |
| Observability | Opik: `@opik.track` tracing + 6 LLM-judge quality metrics (3 custom G-Eval + 3 built-in), gated by `OPIK__ENABLE_EVALUATION`, run as a non-blocking background task, scores attached to the originating trace |
| Config | Pydantic Settings v2, `env_nested_delimiter="__"`, loaded from `.env` |
| Package mgmt | `uv` (pyproject.toml + uv.lock) |
| Container | Docker multi-stage build (`Dockerfile`), FastAPI only |

## Directory map

```
src/
  config.py                          Pydantic Settings — single source of truth for all env config
  configs/feeds_rss.yaml             RSS feed list (name, author, url)
  models/                            Shared Pydantic/SQLAlchemy models (SQL, article, vectorstore)
  api/
    main.py                          FastAPI app factory, lifespan (inits AsyncQdrantVectorStore), CORS, middleware
    routes/                          health_routes.py, search_routes.py — thin HTTP adapters only
    services/                        search_service.py, generation_service.py — business logic
    services/providers/              openrouter_service.py, openai_service.py, huggingface_service.py
    services/providers/utils/        prompts.py (build_research_prompt), evaluation_metrics.py (Opik G-Eval + built-in metrics), messages.py
    models/                          api_models.py (request/response schemas, LLMProvider-typed), provider_models.py (MODEL_REGISTRY, LLMProvider enum)
    exceptions/, middleware/         exception_handlers.py, logging_middleware.py
  infrastructure/
    supabase/                        init_session.py (engine/session), create_db.py, delete_db.py
    qdrant/                          qdrant_vectorstore.py (AsyncQdrantVectorStore — the core class),
                                      create_collection.py, create_indexes.py, delete_collection.py, ingest_from_sql.py
  pipelines/
    flows/                           rss_ingestion_flow.py, embeddings_ingestion_flow.py (Prefect @flow)
    tasks/                           fetch_rss.py, ingest_rss.py, ingest_embeddings.py (Prefect @task)
  utils/                             text_splitter.py (LangChain wrapper), logger_util.py (Loguru/Prefect logger)
frontend/app.py                      Gradio UI (search/ask + article_author filter), talks to FastAPI via BACKEND_URL
tests/                                unit/ (test_fastapi.py fully mocked, test_evaluation_metrics.py,
                                      test_fetch_rss_entries.py), integration/, test_models/, conftest.py
main.py                              Stub scaffold from `uv init` — NOT the app entry point, unused
test_prefect.py                      Ad-hoc debug script, not a real test — excluded from collection via
                                      pytest's `testpaths = ["tests"]` (pyproject.toml), so it no longer
                                      accidentally runs as a "passing test" when you invoke bare `pytest`
static/                              Images used in README (diagrams, screenshots)
```

Run the app via `python -m src.api.main` (or `make run-api`) and `python -m frontend.app`
(or `make run-gradio`), **not** via the root `main.py`.

## Configuration system

Everything funnels through `src/config.py::settings` (a frozen `pydantic_settings.BaseSettings`
singleton). Nested settings classes map to env vars via double-underscore delimiter, e.g.
`SUPABASE_DB__HOST` → `settings.supabase_db.host`. Classes: `SupabaseDBSettings`, `QdrantSettings`,
`RSSSettings`, `TextSplitterSettings`, `JinaSettings`, `HuggingFaceSettings`, `OpenAISettings`,
`OpenRouterSettings`, `OpikObservabilitySettings`. RSS feeds are additionally loaded from
`src/configs/feeds_rss.yaml` via a `model_validator(mode="after")` and merged into `settings.rss.feeds`.

`OpikObservabilitySettings` has three fields: `api_key`, `project_name`, `workspace` (env
`OPIK__WORKSPACE`, defaults to `"default"` — **must match your actual Comet/Opik workspace name**
or traces/evaluation scores silently go to the wrong workspace and never appear on the dashboard),
and `enable_evaluation` (env `OPIK__ENABLE_EVALUATION`, defaults to `False` — gates the whole G-Eval
pipeline in `evaluation_metrics.py`).

`api_models.py` also defines `MAX_RESULT_LIMIT = 25`, used as the `le=` bound on every `limit`
field, plus `max_length=2000` on `query_text` fields — request-size guardrails against the
overfetch multipliers described below.

**Settings are read once at process startup and frozen** — editing `.env` while `make run-api` is
already running has no effect until you restart the process.

`.env.example` documents all variables; copy to `.env` before running anything (the Makefile
hard-fails via a `$(error ...)` guard if `.env` is missing).

## Data flow (ingestion)

```
feeds_rss.yaml → rss_ingest_flow (Prefect)
  → fetch_rss_entries (per feed, parallel via .map): HTTP GET → BeautifulSoup XML parse
    → paywall detection (skips "read more" self-links) → markdownify → ArticleItem
  → ingest_from_rss: batches (RSS__BATCH_SIZE) → SQLAlchemy bulk_save_objects → Supabase

qdrant_ingest_flow (Prefect, async)
  → resolves from_date: CLI arg > last successful Prefect run (via Prefect API) > RSS__DEFAULT_START_DATE
  → ingest_qdrant task → AsyncQdrantVectorStore.ingest_from_sql:
      paginate SubstackArticle rows → TextSplitter.split_text (chunk_size=4000, overlap=200,
      Markdown-aware separators) → dedupe against existing Qdrant point IDs
      (deterministic UUID from sha256(url + chunk_text)) → dense + sparse embed
      concurrently (asyncio.to_thread) → batched Qdrant upsert (Dense + Sparse named vectors)
```

## Request lifecycle (Q&A)

```
Gradio (frontend/app.py) → POST /search/ask or /search/ask/stream
  → search_routes.py → query_with_filters (search_service.py):
      dense_vectors([query]) + sparse_vectors([query]) → Qdrant query_points with
      FusionQuery(fusion=RRF) over two Prefetch branches (Dense, Sparse), optional
      Filter (feed_author=, feed_name=, article_author= as MatchAny, title_keywords= as
      MatchText) → dedupe by point ID → list[SearchResult]
  → generation_service.py:
      build_research_prompt(contexts, query) → MODEL_REGISTRY.get_config(provider)
      → dispatch to provider module (generate_* / stream_*)
      → (OpenRouter only) schedule_evaluation(query, answer, context_chunks) — fire-and-forget,
        never awaited, so it adds zero latency to the response. If enabled, runs 6 metrics
        concurrently (faithfulness/coherence/completeness via custom G-Eval, plus
        hallucination/answer_relevance/usefulness via Opik's built-in metrics) using OpenAI
        gpt-4o as judge, then attaches the scores to the request's Opik trace as feedback
        scores. Skipped entirely if OPIK__ENABLE_EVALUATION is false or no OpenAI key is set.
  → AskResponse{answer, sources, model, finish_reason} or SSE-style plain-text stream
```

**Evaluation only attaches to a trace for the non-streaming `/ask` path.** `schedule_evaluation`
captures the current Opik trace ID via `opik_context.get_current_trace_data()`, which only exists
while inside `generate_answer`'s `@opik.track` span. `get_streaming_function`'s `@opik.track`
decorator only wraps the synchronous construction of the `stream_gen` closure — the actual
streaming happens after that span has already closed — so for `/ask/stream`, evaluation still runs
and is still logged, but there's no active trace to attach the scores to (a warning is logged
instead). This is a known, not-yet-fixed gap.

Streaming protocol (plain-text prefixes consumed by `frontend/app.py::call_ai`):
- `__model_used__:<model>` — first chunk, identifies actual model used (esp. for auto-routing)
- `__error__` — provider error occurred
- `__truncated__` — response hit `max_completion_tokens` (native_finish_reason == "length")
- anything else — plain answer text

## Component dependencies (high level)

```
routes → services → (search_service → AsyncQdrantVectorStore; generation_service → providers/*)
AsyncQdrantVectorStore → fastembed (dense/sparse local models), AsyncQdrantClient, TextSplitter
providers/* → openai.AsyncOpenAI (OpenRouter/OpenAI) or huggingface_hub.AsyncInferenceClient,
              wrapped by opik.integrations.openai.track_openai for tracing
pipelines/flows → pipelines/tasks → infrastructure/{supabase,qdrant}
Everything reads config from src/config.py::settings (imported, never re-instantiated)
```

`app.state.vectorstore` (an `AsyncQdrantVectorStore`) is created once in FastAPI's `lifespan` and
injected into route handlers via `request.app.state.vectorstore` — there is no DI framework, just
app state.

## Database schema (Supabase / Postgres)

Single table `substack_articles` (`src/models/sql_models.py::SubstackArticle`):
`id` (PK, bigint), `uuid` (unique), `feed_name`, `feed_author`, `article_authors` (Postgres
`ARRAY(String)`), `title`, `url` (unique), `content` (Text, Markdown), `published_at`, `created_at`.
No migrations tool (Alembic etc.) — `create_db.py` just does `Base.metadata.create_all` guarded by
an `inspect()` existence check.

## Vector store (Qdrant)

Collection config (`AsyncQdrantVectorStore.__init__` / `create_collection`):
- Named vectors: `"Dense"` (768-dim, cosine) + `"Sparse"` (IDF-modified sparse, BM25 model)
- INT8 scalar quantization (quantile 0.99, not always_ram)
- Bulk-load optimization: HNSW `m=0` and `optimizers.indexing_threshold=0` at creation time
  (disables indexing during bulk upload), then `enable_hnsw()` re-enables `m=16`,
  `indexing_threshold=20000` post-load — must run `make qdrant-create-index` after first bulk ingest
- Payload indexes: keyword indexes on `feed_author`, `article_authors`, `feed_name`; a `TEXT` index
  with Snowball English stemming on `title` (used for `MatchText` substring/keyword filtering)
- Point IDs are deterministic (`uuid5`-style hash of `url + chunk_text`), enabling idempotent
  re-ingestion — `ingest_from_sql` checks existing IDs via `client.retrieve` before embedding

Two search functions in `search_service.py`, both hybrid RRF fusion over Dense+Sparse prefetch
branches with the same filter applied to both branches. Filters: `feed_author`/`feed_name` as
`MatchValue`, `article_author` (list) as `MatchAny` against the `article_authors` payload field,
`title_keywords` as `MatchText`.
- `query_with_filters` — dedupes by point ID, `fetch_limit = limit * 100`
- `query_unique_titles` — dedupes by article title, `fetch_limit = limit * 280`

Both fetch-limit multipliers are still hardcoded magic numbers with no pagination, but `limit`
itself is now bounded (`MAX_RESULT_LIMIT = 25` in `api_models.py`), capping the worst-case overfetch
at 25 × 280 = 7,000 points instead of being fully unbounded.

## Streaming pipeline

`get_streaming_function` (generation_service.py) returns a closure producing an
`AsyncGenerator[str, None]`. Each provider module (`openrouter_service.py`, `openai_service.py`,
`huggingface_service.py`) exposes matching `stream_<provider>()` generators. The route wraps this
in a raw `StreamingResponse(..., media_type="text/plain")` (not true SSE — just chunked plain text)
with `await asyncio.sleep(0)` between yields to keep the event loop responsive. Only the OpenRouter
path runs evaluation post-stream (buffers all chunks, then calls `schedule_evaluation` on the
joined output) — fire-and-forget, so it never delays the stream's completion. As noted above, the
streaming path's evaluation results have no active Opik trace to attach to, so they're only logged
locally, not shown on the dashboard (unlike the non-streaming `/ask` path).

## Deployment architecture

- **Local**: `make run-api` + `make run-gradio` in two terminals; Prefect flows run via
  `make ingest-rss-articles-flow` / `make ingest-embeddings-flow [FROM_DATE=...]`
- **Docker**: `Dockerfile` is a two-stage `uv`-based build producing a slim Python 3.12 image,
  running `uvicorn src.api.main:app` on port 8080, with `HF_HOME`/`FASTEMBED_CACHE` pointed at
  `/tmp` (required — Cloud Run's default HF cache dir is read-only) and a container `HEALTHCHECK`
  hitting `/health`. Frontend/Gradio is **not** containerized.
- **Cloud Run / CI/CD**: README/DOCUMENTATION.md describe `deploy_fastapi.sh`,
  `cloudbuild_fastapi.yaml`, and `.github/workflows/{ci,cd}.yml` — **none of these exist in this
  checkout**. `.github/workflows/` is empty. Treat any doc references to Cloud Run deployment or
  CI pipelines as aspirational/course-template content, not current repo state.
- **Prefect deployment**: `prefect-cloud.yaml` / `prefect-local.yaml` (referenced by
  `make deploy-cloud-flows` / `make deploy-local-flows`) **do not exist yet** — those Makefile
  targets will fail until the files are added.

## Known gaps / current state caveats

- No CI/CD workflows, no Cloud Run deploy script/build config, no Prefect deployment yaml, no
  `.pre-commit-config.yaml` — despite being referenced in README/Makefile/DOCUMENTATION.md.
- **Streaming evaluation doesn't attach to an Opik trace** — see "Request lifecycle" and "Streaming
  pipeline" above. `/ask/stream` computes and logs scores locally but they never reach the
  dashboard; only non-streaming `/ask` does. Not yet fixed.
- **`OPIK__WORKSPACE` must match your actual Comet/Opik workspace name.** It defaults to `"default"`
  if unset — if your real workspace has a different name, traces and evaluation feedback scores are
  silently sent to the wrong (or a nonexistent) workspace and never show up on your dashboard. This
  was the root cause of an earlier "nothing shows up on Opik" session — confirm by checking your
  app's console for `OPIK: Unauthorized` / 401 messages, which indicate this is still misconfigured.
- **`Settings` is read once at process startup and frozen.** Changing `.env` (e.g.
  `OPIK__ENABLE_EVALUATION`, `OPIK__WORKSPACE`) has no effect on an already-running process — it
  must be restarted.
- `SearchResult.article_author` (singular) is populated from the Qdrant payload key
  `article_authors` (plural) — naming mismatch between the API model and storage schema. The filter
  itself works correctly (wired via `MatchAny` in `search_service.py`); this is just a field-naming
  inconsistency, not a functional bug.
- Dense embedding model default differs slightly between `src/config.py`
  (`BAAI/bge-base-en`) and `.env.example` (`BAAI/bge-base-en-v1.5`) — both 768-dim/compatible but
  not the same model artifact.
- `main.py` (root) and `test_prefect.py` are scratch/scaffold files, not part of the real app or
  test suite — `test_prefect.py` is now excluded from pytest collection (`testpaths` in
  `pyproject.toml`), but don't confuse either with `src/api/main.py` or `tests/`.
- No Alembic/migration tooling for the Supabase schema — schema changes require manual DDL or
  dropping/recreating the table.
- `AsyncQdrantVectorStore.delete_collection()` calls Python's blocking `input()` for confirmation —
  incompatible with any non-interactive/automated invocation.
- No timeouts on outbound LLM provider calls (OpenRouter/OpenAI/HF) — a slow provider can hold a
  request open indefinitely. Observed directly: a real trace took **103 seconds** for a single
  non-streaming OpenRouter completion.
- No auth/rate limiting on the FastAPI endpoints — anyone reaching the deployed URL can call
  `/search/*` (and therefore consume LLM provider credits) with no limit.

### Fixed this session (no longer gaps, kept here so history isn't lost)

- ~~`MODEL_REGISTRY` had no `"openai"` entry~~ — added; `"openai"` now resolves correctly.
- ~~`openai_service.generate_openai` hardcoded `model="gpt-4o-mini"`~~ — now uses
  `config.primary_model`, consistent with `stream_openai`.
- ~~`evaluate_metrics()` unconditionally nuked `settings.openai.api_key`~~ — removed; evaluation is
  now gated by `OPIK__ENABLE_EVALUATION` + a real `OPENAI__API_KEY` check, and actually sends scores
  to Opik (previously it only ever logged locally, even when "working").
- ~~`article_author` was accepted by the API but silently dropped~~ — now a real `MatchAny` filter,
  wired through both routes and both search functions, and exposed in the Gradio UI.
- ~~Provider name was a bare `str`~~ — `AskRequest.provider` is now `LLMProvider` (enum), with a
  case-insensitive `field_validator`; an invalid provider is now a clean 422, not a 500.
- ~~`ALLOWED_ORIGINS` unset produced `[""]`~~ — now correctly resolves to `[]`, with a warning if
  `"*"` is ever combined with `allow_credentials=True`.
- ~~No bounds on `limit`/`query_text`~~ — `limit` is now `ge=1, le=25`, `query_text` capped at
  `max_length=2000`.
- ~~`tests/unit/test_fastapi.py` silently depended on live Qdrant + a live OpenRouter call~~ — fully
  mocked now (fake vectorstore fixture, patched `generate_openrouter`); fast and deterministic.
- ~~`substack_test` table required manual setup, causing 3 real test failures~~ — `conftest.py` now
  auto-creates it via a session-scoped `autouse` fixture.

## Production-ready areas

- Configuration management (Pydantic Settings, typed, `.env`-driven, fails fast on missing values,
  request-size bounds enforced at the API boundary)
- Qdrant hybrid search setup (quantization, deferred HNSW indexing for bulk load, deterministic
  IDs for idempotent re-ingestion, payload indexing strategy, `article_author`/`feed_author`/
  `feed_name`/`title_keywords` filtering)
- RSS ingestion robustness (paywall/truncation detection, malformed-XML tolerance, per-item error
  isolation so one bad entry doesn't kill the whole feed fetch)
- Structured logging (context-aware: Prefect run logger inside flows, Loguru outside) plus request
  logging middleware and centralized exception handlers with consistent JSON error shapes
- Multi-provider LLM abstraction (uniform `generate_*`/`stream_*` interface, registry-based config,
  enum-typed + case-insensitive provider selection)
- LLM answer evaluation (6 metrics, concurrent scoring, non-blocking/fire-and-forget, real Opik
  dashboard integration) for the non-streaming path
- `tests/unit/test_fastapi.py` and `tests/unit/test_evaluation_metrics.py` are fully mocked/offline
  — no live service dependency, fast, deterministic

## Areas needing improvement

- Streaming path's evaluation results don't attach to an Opik trace (see "Known gaps")
- No automated deployment path (CI/CD, Cloud Run, Prefect Cloud deployment configs are all missing)
- No pagination/cursor support on search endpoints; large `limit` values are handled via
  brute-force overfetch multipliers (now at least bounded by `MAX_RESULT_LIMIT`)
- No retry/backoff around external LLM provider calls (OpenRouter/OpenAI/HF) at the service layer
  — only Prefect tasks have `retries=2` — and no request timeouts either (see "Known gaps")
- No auth/rate limiting on the FastAPI endpoints (CORS is configured but anyone can call `/search/*`)
- Test suite still has gaps: no tests around Qdrant search/ingestion (`qdrant_vectorstore.py`),
  the individual provider services (`openrouter_service.py`/`openai_service.py`/
  `huggingface_service.py`), or the streaming response path — `test_fastapi.py` and
  `test_evaluation_metrics.py` cover the API/evaluation layer well, but that's it
- Frontend and backend versioning aren't coordinated (no shared schema/OpenAPI client generation)

## Common commands

```bash
make run-api                        # FastAPI on :8080
make run-gradio                     # Gradio on :7860
make supabase-create / -delete      # Table lifecycle
make qdrant-create-collection       # Init collection (HNSW disabled)
make qdrant-create-index            # Enable HNSW + payload indexes (run after bulk ingest)
make ingest-rss-articles-flow       # RSS → Supabase
make ingest-embeddings-flow [FROM_DATE=YYYY-MM-DD]   # Supabase → Qdrant
make unit-tests / integration-tests / all-tests
make all-check / all-fix            # ruff + mypy
uv run pytest -m unit / -m integration   # marker-based selection (partial overlap with folders —
                                          # not every test file is marked yet)
```
