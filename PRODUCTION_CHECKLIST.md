# Production-Level RAG Architecture Checklist

> Reference checklist for this project's engineering maturity. **Scope note:** this is a
> personal project intended for a portfolio/resume, not an enterprise system serving many
> concurrent users — so the pending list below is deliberately short and prioritized, not
> an exhaustive enterprise wishlist. Items that would only matter at real multi-user scale
> (distributed rate limiting, pagination, migration tooling, etc.) are called out explicitly
> as **out of scope for now**, with reasoning, rather than silently omitted or left as clutter.
>
> Updated 2026-08-03 (later same day). Cross-reference [CLAUDE.md](CLAUDE.md) and [ARCHITECTURE.md](ARCHITECTURE.md)
> for implementation detail on anything marked done. The original README.md describes a course
> template targeting Google Cloud Run — that's superseded here: this build targets AWS and has
> diverged substantially from the course's starting point (security hardening, a real 6-metric
> evaluation pipeline, and multiple bug fixes not present in the original).

Legend: ✅ Implemented · ⏳ Pending (prioritized) · 🚫 Out of scope for now

---

## What's implemented (the substance of the resume story)

**Core RAG pipeline**
- ✅ Hybrid search — dense (Fastembed) + sparse (BM25), fused via Qdrant's native RRF
- ✅ Metadata filtering — feed author/name, article author (`MatchAny`), title keywords
- ✅ Deduplication (by point ID or by article title), idempotent ingestion (deterministic point IDs)
- ✅ Markdown-aware chunking, bounded overfetch (`MAX_RESULT_LIMIT`)
- ✅ Cross-encoder re-ranking — RRF's top 25 candidates are re-scored with a local Fastembed cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) before truncating to the requested limit, since RRF fusion never actually reads chunk text against the query. Verified concretely on a real query ("Explain me Langchain?"): re-ranking surfaced two substantially more relevant chunks that weren't even in the top 5 under raw RRF, producing a noticeably richer answer.
- ✅ Semantic caching on `/search/ask` — an in-memory cache keyed on exact-match request context (provider/model/filters/limit) plus cosine similarity on the query's dense embedding; a near-duplicate question skips retrieval and generation entirely. Threshold (0.92) tuned empirically against this project's real embeddings, not guessed — 0.95 was measured too strict to catch an obvious paraphrase. Deliberately in-process (no Redis), and scoped to the non-streaming endpoint only.

**LLM provider layer**
- ✅ Multi-provider abstraction (OpenRouter/OpenAI/Hugging Face) via a uniform `generate_*`/`stream_*` contract and a registry (`MODEL_REGISTRY`)
- ✅ Enum-typed, case-insensitive provider selection — invalid input is a clean 422, not a 500
- ✅ Streaming support across all three providers
- ✅ Timeouts on every outbound provider call (previously: none — a real hung request was observed and fixed)
- ✅ Retry with exponential backoff (+ jitter) on all three providers' non-streaming calls — up to 3 attempts, only for transient failures (connection errors, timeouts, rate limits, 5xx); auth/bad-request errors fail immediately since a retry can't fix those. Verified live against the real decorated function (not just mocks): a simulated `RateLimitError` on attempts 1-2 recovered on attempt 3, with real backoff delay observed. Deliberately scoped to non-streaming only — retrying after chunks have already been yielded isn't safe to do transparently.

**Evaluation & observability** — the standout piece for an interview
- ✅ Full LLM tracing (`@opik.track` + `track_openai`)
- ✅ Automated quality evaluation: 6 metrics (faithfulness, coherence, completeness, hallucination, answer relevance, usefulness) — 3 custom G-Eval + 3 Opik built-ins, scored concurrently
- ✅ Fire-and-forget background scoring — zero added latency to user-facing responses
- ✅ Feedback scores actually reach the Opik dashboard, attached to the originating trace (a real bug was found and fixed here: scores were computed but never sent anywhere)
- ✅ Correct workspace/project configuration (a second real bug: traces were silently going to the wrong workspace)

**Security**
- ✅ API key authentication (fails closed if unconfigured — a missing key is a loud error, never silent open access)
- ✅ Rate limiting (per-IP, `X-Forwarded-For`-aware for deployment behind a load balancer)
- ✅ CORS fixed (was silently producing an invalid empty-origin config)
- ✅ Request-size bounds on every user-facing input
- ✅ Gradio UI login (`GRADIO_AUTH_USERS`, comma-separated `user:pass` pairs) — closes the gap the API key didn't cover: previously anyone reaching the Gradio URL had full free access to the app regardless of the backend's auth. Authorization model is deliberately flat (any valid login = full access, no roles) since a personal project doesn't need tiers; optional locally (no login if unset) so local dev isn't hindered, required once actually exposed to others.

**Reliability & testing**
- ✅ Health/readiness endpoints, centralized exception handling
- ✅ 47 passing unit/mocked tests (plus integration tests, manual-only in CI): fully-mocked API layer, evaluation pipeline (9), auth/rate-limit dependencies (8), Gradio auth parsing (8), re-ranking (5), semantic caching (9), provider retry/backoff (5), integration tests with auto-provisioned test DB
- ✅ CI pipeline (`.github/workflows/ci.yml`) — `ruff check` + `ruff format --check` + `mypy` + `pytest -m "not integration"` on every push/PR to `main`, two parallel jobs (lint, test). Getting here required actually fixing mypy rather than just running it: added `[tool.mypy]` config (was entirely missing — bare `mypy`/`make mypy` didn't even run before), and fixed 5 real pre-existing type errors along the way — including a genuine bug where a Prefect `FlowFilter(name=dict(eq_=...))` call silently built a no-op filter (`FlowFilterName` has no `eq_` field, so pydantic dropped it), now fixed to actually filter server-side instead of relying entirely on a client-side re-filter.
- ⚠️ Not yet done: branch protection requiring this CI check to pass before merge — a GitHub repo setting, not something committable; worth turning on once you're comfortable with the workflow.

**Documentation**
- ✅ `CLAUDE.md` / `ARCHITECTURE.md` — accurate, maintained architecture reference (not just aspirational docs)

---

## Priority pending (do these — highest resume value per hour spent)

Ordered so the app is built out and hardened locally first; deployment-related items
(going live, CD, automated ingestion) are pushed to the later stages deliberately, once
there's a stable, feature-complete app worth deploying and automating.

1. **A small golden eval dataset (~20 query/answer pairs).** Completes the evaluation story you already built. Right now you have live scoring; adding regression testing ("did this prompt change make things worse?") turns it into a genuinely complete MLOps narrative.
2. **Date-range filtering.** Small, natural feature completion; `published_at` is already stored, just not exposed as a filter.
3. **Ship it to AWS (App Runner) — not Cloud Run as README.md describes.** By far the biggest deployment differentiator. "Built a RAG system" is good; "built and deployed a RAG system, here's the live URL" is what actually stands out. Needs: fix `reload=True` in `src/api/main.py` first (dev-only flag, must not run in a deployed container), then ECR + App Runner for the backend, containerize Gradio as a second small service. This is the README's most out-of-date claim — worth fixing there too once deployed. Deliberately placed here, after the app is feature-complete and hardened — deploying earlier just means redeploying repeatedly as the app above changes.
4. **CD pipeline (`cd.yml`).** Builds on #3 and CI: once there's a real deployment and CI is green, automate it — build the Docker image, push to ECR, redeploy the App Runner service, triggered on merge to `main` (gated on CI passing first). This is what turns "I deployed it once by hand" into "I have a real deployment pipeline," a meaningfully stronger resume claim.
5. **Fully automated data ingestion pipeline.** Right now RSS + embedding ingestion only run via manual `make` commands — not "production" by any real definition. The project already has Prefect wired in with `deploy-cloud-flows`/`deploy-local-flows` Makefile targets, but the `prefect-cloud.yaml` file they reference doesn't exist yet. Concretely: write `prefect-cloud.yaml` defining both flows with cron schedules, deploy via the existing `make deploy-cloud-flows` target, and run a worker (Prefect Cloud's managed execution if available on the free tier, otherwise one small persistent worker process) so new articles actually appear on their own, with no one running a command by hand. Saved for last since it's an operational concern independent of the app's own feature set.

## Nice-to-have (only after the above, don't block on these)

- Test coverage for `qdrant_vectorstore.py` and the remaining provider-service behavior (retry logic itself is now covered — `generate_*`/streaming happy paths and edge cases beyond retry still aren't)

## Out of scope for now (and why)

- 🚫 **Distributed/Redis-backed rate limiting** — the in-memory limiter only matters once you run multiple instances; a personal project on a single App Runner instance doesn't need this.
- 🚫 **Pagination/cursor support on search** — not meaningful at this traffic scale; the existing `MAX_RESULT_LIMIT` bound is sufficient.
- 🚫 **Alembic migrations** — one table, low schema-change frequency; manual DDL is fine here and adding a migration framework would be process overhead with no real payoff.
- 🚫 **Query rewriting/HyDE, MMR diversity, citation-grounding validation** — legitimate RAG improvements, but diminishing returns past what re-ranking and semantic caching (both already done) already demonstrate.
- 🚫 **Circuit breakers / cross-provider automatic fallback** — retry/backoff (now implemented) captures most of the reliability value; a full fallback chain is more complexity than a personal project's traffic justifies.

---

## Suggested order

Build and harden locally first; deployment comes once there's something worth deploying:

1. ~~Gradio auth~~ ✅ done
2. ~~CI pipeline~~ ✅ done
3. ~~Re-ranking~~ ✅ done
4. ~~Semantic caching~~ ✅ done
5. ~~Retry/backoff~~ ✅ done
6. Golden eval dataset
7. Date-range filtering
8. Fix `reload=True` → deploy backend + Gradio to AWS App Runner
9. CD pipeline — automate future deploys now that #8 exists
10. Automated ingestion (Prefect Cloud schedule + worker)
