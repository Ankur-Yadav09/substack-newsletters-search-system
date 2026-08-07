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
- ✅ Metadata filtering — feed author/name, article author (`MatchAny`), title keywords, publish date range (`DatetimeRange`, requires a payload index on `published_at` — Qdrant rejects range filters on unindexed fields)
- ✅ Deduplication (by point ID or by article title), idempotent ingestion (deterministic point IDs)
- ✅ Markdown-aware chunking, bounded overfetch (`MAX_RESULT_LIMIT`)
- ✅ Cross-encoder re-ranking — RRF's top 25 candidates are re-scored with a local Fastembed cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) before truncating to the requested limit, since RRF fusion never actually reads chunk text against the query. Verified concretely on a real query ("Explain me Langchain?"): re-ranking surfaced two substantially more relevant chunks that weren't even in the top 5 under raw RRF, producing a noticeably richer answer.
- ✅ Semantic caching on `/search/ask` — an in-memory cache keyed on exact-match request context (provider/model/filters/date range/limit) plus cosine similarity on the query's dense embedding; a near-duplicate question skips retrieval and generation entirely. Threshold (0.92) tuned empirically against this project's real embeddings, not guessed — 0.95 was measured too strict to catch an obvious paraphrase. Deliberately in-process (no Redis), and scoped to the non-streaming endpoint only.

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
- ✅ Golden eval dataset (`make golden-eval` / `make golden-eval-save-baseline`) — a fixed set of queries run through the real retrieval+generation pipeline and scored on the same 6 metrics, compared against a saved baseline to catch regressions after a prompt/retrieval change. 6 of the queries also have a curated `expected_output` (grounded in real retrieved chunks, not invented) so they additionally get scored on Context Precision/Context Recall — genuine retrieval-quality metrics, distinct from the 6 generation-quality metrics used elsewhere.

**Security**
- ✅ API key authentication (fails closed if unconfigured — a missing key is a loud error, never silent open access)
- ✅ Rate limiting (per-IP, `X-Forwarded-For`-aware for deployment behind a load balancer)
- ✅ CORS fixed (was silently producing an invalid empty-origin config)
- ✅ Request-size bounds on every user-facing input
- ✅ Gradio UI login (`GRADIO_AUTH_USERS`, comma-separated `user:pass` pairs) — closes the gap the API key didn't cover: previously anyone reaching the Gradio URL had full free access to the app regardless of the backend's auth. Authorization model is deliberately flat (any valid login = full access, no roles) since a personal project doesn't need tiers; optional locally (no login if unset) so local dev isn't hindered, required once actually exposed to others.

**Reliability & testing**
- ✅ Health/readiness endpoints, centralized exception handling
- ✅ 70 passing unit/mocked tests (plus integration tests, manual-only in CI): fully-mocked API layer (3), evaluation pipeline (9), auth/rate-limit dependencies (8), Gradio auth parsing (8), re-ranking (5), semantic caching (10), provider retry/backoff (5), golden eval harness (11), search filter construction (4), request validation incl. date-range (7), integration tests with auto-provisioned test DB
- ✅ CI pipeline (`.github/workflows/ci.yml`) — `ruff check` + `ruff format --check` + `mypy` + `pytest -m "not integration"` on every push/PR to `main`, two parallel jobs (lint, test). Getting here required actually fixing mypy rather than just running it: added `[tool.mypy]` config (was entirely missing — bare `mypy`/`make mypy` didn't even run before), and fixed 5 real pre-existing type errors along the way — including a genuine bug where a Prefect `FlowFilter(name=dict(eq_=...))` call silently built a no-op filter (`FlowFilterName` has no `eq_` field, so pydantic dropped it), now fixed to actually filter server-side instead of relying entirely on a client-side re-filter.
- ⚠️ Not yet done: branch protection requiring this CI check to pass before merge — a GitHub repo setting, not something committable; worth turning on once you're comfortable with the workflow.

**Documentation**
- ✅ `CLAUDE.md` / `ARCHITECTURE.md` — accurate, maintained architecture reference (not just aspirational docs)

---

## Priority pending (do these — highest resume value per hour spent)

Ordered so the app is built out and hardened locally first; deployment-related items
(going live, CD, automated ingestion) are pushed to the later stages deliberately, once
there's a stable, feature-complete app worth deploying and automating.

1. **Ship it to AWS — not Cloud Run as README.md describes.** By far the biggest deployment differentiator. "Built a RAG system" is good; "built and deployed a RAG system, here's the live URL" is what actually stands out. Given a **free-tier-only AWS account**, this now targets a single free-tier-eligible EC2 instance (`t2.micro`/`t3.micro`) running both containers via `docker-compose` behind Caddy — not App Runner, which has no free tier at all and would be real out-of-pocket spend from day one. Images are built and pushed to **ECR** (nearly free — a few cents/month, not a real cost concern like App Runner was), and the EC2 instance only ever pulls and runs them, since building on a 1 GiB-RAM box is a real risk in itself. Needs: fix `reload=True` in `src/api/main.py` first (dev-only flag), containerize Gradio (currently not containerized at all), add `docker-compose.yml` + a `Caddyfile`. This is the README's most out-of-date claim — worth fixing there too once deployed. Deliberately placed here, after the app is feature-complete and hardened — deploying earlier just means redeploying repeatedly as the app above changes.
2. **CD pipeline (`cd.yml`).** Builds on #1 and CI: once there's a real deployment and CI is green, automate it — a GitHub Actions job that builds both images, pushes to ECR, then SSHes into the EC2 instance to run `docker compose pull && docker compose up -d`, triggered on merge to `main` (gated on CI passing first). This is what turns "I deployed it once by hand" into "I have a real deployment pipeline," a meaningfully stronger resume claim.
3. **Fully automated data ingestion pipeline.** Right now RSS + embedding ingestion only run via manual `make` commands — not "production" by any real definition. The project already has Prefect wired in with `deploy-cloud-flows`/`deploy-local-flows` Makefile targets, but the `prefect-cloud.yaml` file they reference doesn't exist yet. Concretely: write `prefect-cloud.yaml` defining both flows with cron schedules, deploy via the existing `make deploy-cloud-flows` target, and run a worker (Prefect Cloud's managed execution if available on the free tier, otherwise one small persistent worker process) so new articles actually appear on their own, with no one running a command by hand. Saved for last since it's an operational concern independent of the app's own feature set.

---

## Deployment plan — EC2 free tier (detailed reference for item #1 above)

**Phase 1 is done and verified. Phase 2 (real AWS resources) is next, not yet executed.**
Captured here in full so implementation can resume from this exact plan at any point.
**Revised from an earlier App Runner-based plan** once it came to light
the AWS account is free-tier-only: App Runner has **no free tier at all** (billed per
vCPU-hour/GB-hour from the first second — the earlier ~$25-50/mo estimate would have been real
spend, not a discounted cost), so the architecture changed to a single free-tier-eligible EC2
instance. Real cost target: **$0/month** during the 12-month free-tier window (750 hrs/month of
`t2.micro`/`t3.micro` = one instance running continuously, at $0; verify your account's exact
free-tier terms in the AWS Billing console since AWS has changed this program over time).
CD (`cd.yml`) is explicitly a separate, later item — this is just the first manual deploy.

**Architecture**: one EC2 instance running three containers via `docker-compose` — `backend`
(FastAPI, :8080), `frontend` (Gradio, :7860), and `caddy` (official `caddy:latest` image, reverse
proxy, :80) in front of both, so only port 80 needs opening in the security group instead of two
separate ports. **Images are built and pushed to ECR** (two repos: backend, frontend) — initially
from the user's own machine (real RAM/CPU), later from GitHub Actions once the CD step exists.
The EC2 instance only ever runs `docker compose pull` + `up`, never `build` — building on a 1
GiB-RAM instance (`uv sync`, compiling, layering) is a real risk on its own, separate from
whether it can run the containers afterward. ECR cost at this scale is negligible (~500 MB/month
free for 12 months, then ~$0.10/GB-month; pulls from ECR into an EC2 instance in the same region
are free) — this is not the kind of real recurring spend App Runner was, so there was no actual
cost reason to avoid it. EC2 authenticates to ECR via an **IAM instance role** (no long-lived AWS
credentials stored on the box). The same `docker-compose.yml` serves both flows: it declares both
`build: .` and `image: <ecr-url>/...:latest` per service — `docker compose build` locally builds
and tags that name; `docker compose pull` on EC2 fetches that exact tag without ever building.
Caddy runs in plain HTTP mode initially (no domain yet); pointing a real domain at the instance
later upgrades it to automatic HTTPS with a one-line `Caddyfile` change, no code changes.

**Known constraint, stated honestly rather than glossed over**: `t2.micro`/`t3.micro` only has
**1 GiB of RAM**. Running the backend (which loads 3 ML models — dense embedder, sparse
embedder, reranker — into memory) plus the frontend plus Caddy on one box is tight; a swap file
is added as insurance against OOM kills rather than assuming it'll comfortably fit. Building
images elsewhere (not on this box) removes one major source of memory pressure, but runtime
memory for the loaded models remains a real constraint regardless.

**A real security issue found during planning (fix first, before anything else):**
`.dockerignore` does not exclude `.env`. The Dockerfile's builder stage does `COPY . .` from the
build context. Whether the build happens on the user's own machine or in GitHub Actions, a real
`.env` (or real secrets in CI env) must never end up baked into an image layer that gets pushed
to ECR — a registry is a persistent artifact store, not an ephemeral build context. Must be fixed
before the first build meant for ECR.

### Phase 1 — Local code fixes ✅ DONE (no AWS involved, verified before touching AWS at all)

1. **`.dockerignore`**: add `.env` (keep `.env.example` included — it has no real values).
   Verify with `docker history --no-trunc <image>` on a rebuilt image that no secret lands in
   a layer.
2. **`src/api/main.py`**: `if __name__ == "__main__":` hardcodes `reload=True` with a stale
   "For Cloud Run" comment. The Docker `CMD` bypasses this (calls uvicorn directly, no
   `--reload`), so the current image isn't actually broken by it — but it's a landmine if the
   entrypoint ever changes, and the comment is wrong. Fix: gate behind a `UVICORN_RELOAD` env
   var (default `false`), fix the comment. `Makefile`'s `run-api` sets `UVICORN_RELOAD=true` so
   local hot-reload is unchanged.
3. **`frontend/app.py`**: has two `if __name__ == "__main__":` blocks — one live
   (`demo.launch(auth=GRADIO_AUTH_USERS)`, defaults to 127.0.0.1:7860) and one fully commented
   out that already anticipates deployment (`server_name="0.0.0.0"`, `PORT` env var). Merge into
   one: `demo.launch(auth=GRADIO_AUTH_USERS, server_name="0.0.0.0",
   server_port=int(os.environ.get("PORT", 7860)))` — 7860 is Gradio's real default, so local
   behavior is unchanged. Remove the dead commented block.
4. **New `frontend/Dockerfile`**: mirror the root `Dockerfile`'s multi-stage `uv` pattern
   exactly, differing only in `CMD ["python", "-m", "frontend.app"]` and `EXPOSE 7860`. Accepted
   trade-off: this image also installs the backend's heavier deps (fastembed, qdrant-client,
   sqlalchemy) even though `frontend/app.py` only calls the backend over HTTP and never imports
   them — larger image than strictly necessary, not a correctness problem, not worth a
   `pyproject.toml` dependency-group split for a personal project.
5. **New `docker-compose.yml`** at repo root: `backend`, `frontend`, `caddy` services. `backend`
   and `frontend` each declare both `build: .` (for local dev) and `image: <ecr-url>/...:latest`
   (so the same file works for `docker compose build` locally and `docker compose pull` on EC2);
   `env_file: .env` on both so secrets are read from one file, not duplicated into the compose
   file itself; `caddy` uses the official `caddy:latest` image directly (no custom Dockerfile
   needed), `depends_on` both, and mounts the `Caddyfile`.
6. **New `Caddyfile`**: path-based routing, plain HTTP (`:80`) for now — `/search/*`, `/health`,
   `/ready` → `backend:8080`; everything else → `frontend:7860`. Comment noting that adding a
   real domain as the site address (instead of `:80`) is all that's needed later for automatic
   HTTPS.
7. **Local verification before any AWS step**: `docker compose up --build` locally, hit
   `http://localhost/health` through Caddy, confirm the frontend loads through Caddy too,
   confirm a real question round-trips end-to-end; re-run the `docker history` check from step 1.

**What actually happened during Phase 1 verification (real findings, not hypothetical):**
- `.dockerignore` already excluded `.env` — verified further by running each built image and
  checking `/app/.env` doesn't exist inside it (only `.env.example` does).
- `docker compose up -d` worked first try: Caddy correctly waited for backend + frontend to
  report `healthy` (via `depends_on: condition: service_healthy`, reading the Dockerfiles'
  `HEALTHCHECK`s) before starting.
- A real `/search/unique-titles` call through Caddy returned real, reranked results from the
  live corpus — proving the backend container reaches the real external Qdrant/Supabase from
  inside Docker.
- Confirmed the frontend container reaches the backend via `http://backend:8080` (Docker's
  internal DNS), not `localhost` or Caddy — that call is server-to-server (Python `requests`),
  not something a browser does, so it was never subject to CORS in the first place.
- **Two real gaps found and fixed in the `Caddyfile` that weren't caught by `caddy validate`**
  (which only checks syntax, not routing completeness):
  1. Caddy doesn't log requests by default (only its own startup/admin events) — added a
     `log { output stdout; format console }` block so `docker compose logs caddy` actually shows
     per-request access logs.
  2. `/docs`, `/redoc`, `/openapi.json` (FastAPI's interactive docs) weren't routed to the
     backend — they fell into the catch-all and hit Gradio instead. Since Gradio is *also*
     FastAPI-based internally, its 404 for those paths looked deceptively like it came from the
     real backend. Fixed with a named matcher: `@fastapi_docs path /docs /redoc /openapi.json`
     then `handle @fastapi_docs { reverse_proxy backend:8080 }` (a bare `handle /docs /redoc
     /openapi.json { ... }` fails to parse — `handle` only accepts one path argument; multiple
     OR'd paths need a named matcher).
  3. Also learned: editing `Caddyfile` on disk has zero effect on an already-running container
     (it's a bind mount, read once at startup) — needs `docker compose restart caddy` to
     actually take effect.

### Phase 2 — ECR + EC2 steps (user executes every command themselves via `!`)

Detailed what/why/how for each step below, since these involve real AWS resources and real
(if free-tier) money — worth understanding before running anything, not just copy-pasting.

**Decisions made before starting:**
- **Region: `us-west-2` (Oregon)** — matches the existing Qdrant instance's region. Every live
  `/search/ask` request hits Qdrant, so this minimizes latency for the thing that actually
  matters on every query. Supabase is `ap-south-1` (Mumbai) — a different region — but it's only
  touched by occasional manual `make ingest-*` runs, never by a live request, so its region
  doesn't drive this decision. Trade-off accepted: SSH/setup access from India will have higher
  ping than a local region would, but that only affects setup convenience, not the deployed
  app's actual user-facing performance.
- **SSH key pair: create a new one dedicated to this instance** (via `aws ec2 create-key-pair`
  in step 10), rather than reusing an existing one.

8. **Create two ECR repositories** (backend, frontend) via `aws ecr create-repository` — run
   from the user's own machine.
   - *What*: two empty private "image storage" repos in AWS, one per service — like a private
     Docker Hub.
   - *Why*: this is where the built images live so the EC2 instance can pull them later.
   - *Cost*: negligible (~500 MB/month free for 12 months, then ~$0.10/GB-month).
9. **Authenticate Docker to ECR from the local machine, build, tag, and push both images.** This
   is the "real RAM/CPU" build referenced above — nothing gets built on the EC2 instance itself.
   - *What*: take the already-built-and-verified `substack-backend:local`/`substack-frontend:local`
     images from Phase 1, retag them with ECR's naming format, upload them.
   - *Why*: the user's own machine (or later, GitHub Actions) does the heavy lifting of
     `uv sync`/compiling — not the tiny EC2 instance.
   - *How*: `aws ecr get-login-password | docker login ...`, then `docker tag ... <ecr-url>/...`,
     then `docker push`, for both images.
10. **Launch a `t2.micro`/`t3.micro` EC2 instance** (free-tier-eligible AMI — Amazon Linux or
    Ubuntu) with an **IAM instance role** attached granting ECR pull access (e.g. the
    `AmazonEC2ContainerRegistryReadOnly` managed policy) — this is what lets the instance pull
    from ECR with no long-lived credentials stored on it. Security group: inbound 22 (SSH,
    ideally restricted to the user's own IP) and 80 (HTTP) — no need to open 8080/7860
    individually since Caddy is the single entry point.
    - *What*: the actual virtual machine that runs everything, 24/7.
    - *Decisions needed first*: AWS region, an SSH key pair (new or existing), the IAM instance
      role, and the security group rules above.
11. **Allocate an Elastic IP** (free while attached to a running instance) so the public address
    doesn't change across stop/start/restart.
    - *Why*: without one, the instance's public IP changes every time it's stopped/started —
      bad for a URL meant to be shared/kept. Free as long as it stays attached to a running
      instance; AWS only charges for an allocated-but-unattached one.
12. **SSH in; install Docker + the Compose plugin.**
    - *Why*: the instance starts as a bare OS — nothing about Docker is pre-installed.
13. **Add a swap file** (e.g. 2 GB) given the 1 GiB RAM constraint — `fallocate`/`dd` + `mkswap`
    + `swapon` + an `/etc/fstab` entry so it persists across reboots.
    - *Why*: the honest, not-glossed-over part of this plan — 1 GiB of real RAM is tight for a
      backend that loads 3 ML models into memory. Swap is cheap insurance against an OOM kill.
14. **`git clone` the repo onto the instance** — still needed even though images come from ECR:
    this is where `docker-compose.yml` and `Caddyfile` come from.
    - *Why*: those two files aren't baked into any image — they're the orchestration layer that
      tells Docker which images to pull and how to wire them together.
15. **Create a real `.env` file on the instance directly** (e.g. `nano .env`, or `scp` it up over
    SSH) — never via git, since it's gitignored and must stay that way.
    - *Why*: `docker-compose.yml`'s `env_file: .env` needs this to exist to pass real secrets
      into the containers.
16. **Authenticate Docker to ECR from the instance** (via the IAM instance role — `aws ecr
    get-login-password` works automatically, no `aws configure` needed), then
    `docker compose pull && docker compose up -d`.
    - *What*: the actual "go live" moment — fetches the exact images pushed in step 9 and starts
      all three containers.
17. **End-to-end verification**: curl `http://<elastic-ip>/health`; open `http://<elastic-ip>/`
    in a browser, confirm the Gradio login prompt (if `GRADIO_AUTH_USERS` is set), log in, ask a
    real question, confirm it returns an answer.
    - Same kind of checks already proven locally in Phase 1 — this time against the real
      infrastructure, not assumed to carry over just because it worked locally.

**Explicitly deferred (not part of this step):** CD pipeline (separate item, see above); a real
domain + automatic HTTPS (just a `Caddyfile` + DNS change whenever wanted, no code changes);
splitting `pyproject.toml` into per-service dependency groups to slim the frontend image.

**Files to be touched in Phase 1:** `.dockerignore`, `src/api/main.py`, `Makefile`,
`frontend/app.py`, new `frontend/Dockerfile`, new `docker-compose.yml`, new `Caddyfile`.

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
6. ~~Golden eval dataset~~ ✅ done
7. ~~Date-range filtering~~ ✅ done
8. Fix `reload=True` → deploy backend + Gradio to AWS App Runner
9. CD pipeline — automate future deploys now that #8 exists
10. Automated ingestion (Prefect Cloud schedule + worker)
