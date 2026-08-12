# RAG Systems Field Guide

Every concept this project actually implements — retrieval, hybrid search, evaluation,
reliability, and the deployment plumbing underneath — with real code, real bugs found along the
way, and the questions an interviewer is likely to ask about each one. Organized in the same order
data actually flows through the system: ingestion → retrieval → generation → reliability →
evaluation → API/security → testing → deployment.

An interview Q&A bank (30+ questions, click-to-reveal answers) is at the bottom.

---

## 1. Foundations

### What RAG is — and isn't

*Retrieval-Augmented Generation: ground an LLM's answer in retrieved documents instead of relying
on parametric memory alone.*

An LLM's training data is frozen at some cutoff and it can't cite sources for what it "knows." RAG
fixes both: at query time you **retrieve** relevant chunks from a store you control, then **inject**
them into the prompt so the model generates from real, current, attributable text.

It's not the only fix. **Fine-tuning** changes the model's weights to internalize a domain's
style/knowledge — expensive, doesn't help with facts that change weekly, and still hallucinates.
**Long-context stuffing** (paste everything into a huge context window) skips retrieval but costs
tokens linearly, and quality reliably degrades as context grows ("lost in the middle"). RAG's bet:
retrieval is cheap and precise, so you only pay generation cost for the few chunks that actually
matter.

> **Interview angle:** "When would you fine-tune instead of RAG?" — when the need is a
> *style/format/behavior* change (not facts), or facts that never change and volume justifies the
> training cost. They're also combinable: fine-tune for domain jargon, RAG for facts.

### This project's end-to-end architecture

*Substack RSS feeds → Postgres (Supabase) → chunked + embedded into Qdrant → hybrid search +
rerank → LLM → answer, with evaluation running alongside.*

Two independent pipelines feed one query path. **Ingestion** (Prefect flows) pulls RSS feeds into
a relational store, then chunks and embeds rows into a vector store — this runs on a schedule,
decoupled from user traffic. **Serving** (FastAPI) takes a live question, embeds it, hits Qdrant
with a hybrid query, optionally reranks and caches, builds a prompt from the results, and streams
or returns an LLM answer — all while a judge model scores the answer in the background.

**Why the split matters:** ingestion is slow and bursty (bulk chunking + embedding); serving must
be fast and predictable. Separating them means a slow re-ingest never blocks or slows down a live
user request — they don't even share a process.

---

## 2. Ingestion

### Ingestion robustness

*Real feeds are messy — paywalls, truncated content, malformed XML, one bad item shouldn't kill a
whole run.*

Production ingestion isn't "call an API and store the result" — it's defending against everything
an uncontrolled upstream can do wrong. This pipeline detects paywall/"read more" self-links so
truncated articles aren't stored as if complete, tolerates malformed XML per-feed, and isolates
errors **per item** so one bad entry doesn't abort the whole feed's fetch.

> **Interview angle:** "What happens when one record in a batch is bad?" Per-item error isolation
> (try/except around the unit of work, not the whole batch) is the answer that shows production
> thinking, not toy-script thinking.

### Chunking strategy

*Split long documents into retrievable units small enough to be precise, large enough to keep
context — with overlap so a fact split across a boundary isn't lost.*

This project uses **Markdown-aware** splitting (LangChain's text splitter, separators tuned to
headers/paragraphs, not blind character counts) — a chunk boundary should fall at a semantic
break, not mid-sentence. Config: `chunk_size=4000`, `chunk_overlap=200`. The overlap means a
sentence straddling a cut point still appears whole in at least one chunk.

| Too small | Too large |
|---|---|
| Fragments lose surrounding context; embeddings become noisy/ambiguous. | Dilutes relevance signal — a chunk about five topics scores mediocre on all five queries. |

> **Interview angle:** "Fixed-size vs. semantic chunking?" — fixed-size is simpler and faster but
> blind to structure; semantic/markdown-aware respects document boundaries at the cost of variable
> chunk sizes. Know the size/overlap numbers for your own project and *why* those values, not just
> that they exist.

---

## 3. Retrieval & Vectors

### Dense embeddings

*A neural encoder maps text to a fixed-length vector where semantic closeness becomes geometric
closeness — captures meaning, not just words.*

This project uses Fastembed's `BAAI/bge-base-en` (768-dim, cosine similarity) as a local
bi-encoder — text goes in, a dense float vector comes out, and cosine distance between two
vectors approximates semantic similarity. Bi-encoders embed query and document *independently*
(fast, cacheable, scalable to millions of docs) as opposed to cross-encoders, which score a pair
jointly (much more accurate, far too slow to run over an entire corpus per query).

> **Real detail from this project:** `src/config.py` defaults to `BAAI/bge-base-en` while
> `.env.example` documents `BAAI/bge-base-en-v1.5` — both 768-dim/compatible but not the same
> model artifact. A reminder that "compatible dimensions" doesn't mean "identical model."

### Sparse embeddings / BM25

*A high-dimensional, mostly-zero vector built from term statistics — captures exact
keyword/lexical match, the thing dense embeddings are weakest at.*

Dense embeddings are great at "these mean the same thing" and surprisingly bad at exact terms — a
product SKU, an acronym, a person's name — because those get smoothed away in a 768-dim semantic
space. BM25 (here, Qdrant's `Qdrant/bm25` sparse model) scores term frequency vs. document
frequency directly: rare, exact terms score high, common words are downweighted. It's the "grep
with statistics" half of hybrid search.

> **Interview angle:** Concrete example to have ready — a query containing a specific model name
> or library ("Xenova/ms-marco-MiniLM") is exactly where dense-only retrieval quietly fails and
> BM25 saves it. This project literally depends on both for exactly this reason.

### Hybrid search & RRF fusion

*Run dense and sparse search as separate ranked lists, then merge them by **rank position**, not
raw score — because the two scores aren't on comparable scales.*

You can't just average a cosine score (0–1) with a BM25 score (unbounded, corpus-dependent) — they
mean different things. **Reciprocal Rank Fusion** sidesteps this: each result's fused score is
`Σ 1/(k + rank)` across the lists it appears in. A document ranked #1 in both lists beats one
ranked #1 in only one — RRF rewards consistent relevance across independent signals, using only
where each item placed, never its raw score. Implemented here via Qdrant's native
`FusionQuery(fusion=RRF)` over two `Prefetch` branches.

**Why not just pick one:** dense-only misses exact terms; sparse-only misses paraphrase and
synonymy. Hybrid isn't a compromise between them — it's genuinely better recall than either alone,
at the cost of running two retrieval passes.

### Vector database internals (Qdrant)

*HNSW graph search for approximate nearest neighbors, quantization to shrink memory, deterministic
IDs for idempotent re-ingestion.*

**HNSW** (Hierarchical Navigable Small World) builds a multi-layer graph so nearest-neighbor
search is sub-linear instead of scanning every vector. Building the index is expensive, so this
project *disables* it during bulk load (`m=0`, `indexing_threshold=0`) and re-enables it afterward
(`m=16`, `threshold=20000`) via `enable_hnsw()` — indexing during a giant bulk upsert would
otherwise slow every single insert. **INT8 scalar quantization** (quantile 0.99) trades a little
recall accuracy for a large memory reduction, since float32 vectors at scale get expensive fast.

**Deterministic point IDs** — a hash of `sha256(url + chunk_text)` — mean re-running ingestion on
already-processed content produces the exact same ID, so upserting is naturally idempotent: no
duplicate points, no manual "have I seen this before" bookkeeping beyond the hash itself.

> **Interview angle:** "How do you make ingestion idempotent?" is a very common systems question —
> deterministic IDs derived from content (not an auto-incrementing counter or a random UUID) is
> the clean answer, and this project actually does it.

### Metadata filtering alongside vector search

*Vector similarity narrows by **meaning**; payload filters narrow by **facts** — combining both is
what makes search actually usable.*

"Articles like this" and "articles by this author, published after March" are different
questions. This project layers Qdrant payload filters on top of the same hybrid query:
`MatchValue` for exact fields, `MatchAny` for list membership (article authors), `MatchText` for
keyword/substring matching on title, and `DatetimeRange` for publish-date windows.

> **Real detail from this project:** Qdrant **rejects range filters on unindexed fields** — a
> payload index on `published_at` is a hard prerequisite for date-range filtering to work at all,
> not an optimization. This is the kind of "it just throws until you add an index" detail that
> only shows up by actually building the feature.

---

## 4. Retrieval Quality

### Cross-encoder reranking

*RRF fusion never reads the actual chunk text against the query — a second pass that **does** read
both together catches what rank-position math misses.*

A cross-encoder (here, `Xenova/ms-marco-MiniLM-L-6-v2`, local via Fastembed) takes the query and a
candidate chunk *together* as one input and outputs a single relevance score — far more accurate
than comparing two independently-computed embeddings, but too slow to run over a full corpus. The
pattern: cheap hybrid retrieval narrows millions of chunks to ~25 candidates, then the expensive
cross-encoder re-scores just those 25 before truncating to the requested limit.

**Verified concretely in this project:** on a real query ("Explain me Langchain?"), reranking
surfaced two chunks that weren't even in RRF's top 5, producing a noticeably richer answer — not a
theoretical benefit, an observed one.

### Semantic caching

*Cache on **meaning**, not exact string match — a paraphrased question should still hit the
cache.*

An in-memory cache keyed on exact request context (provider/model/filters/date range/limit) plus
**cosine similarity on the query's dense embedding** — a near-duplicate question skips retrieval
and generation entirely. The similarity threshold (0.92) was tuned empirically against this
project's real embeddings, not guessed: 0.95 was measured too strict to catch an obvious
paraphrase.

> **Interview angle:** "Why not just cache on the exact query string?" — because "what is RAG" and
> "explain RAG to me" are different strings but the same request; string-keyed caching misses the
> majority of real cache-hit opportunity in a Q&A system.

**Real scoping decision:** deliberately in-process (no Redis) and scoped to the non-streaming
endpoint only — a considered tradeoff for a single-instance personal project, explicitly called
out as something that *wouldn't* scale to multiple app instances without a shared cache backend.

### Overfetch multipliers & result bounds

*Deduplication after fusion means you must fetch more than you'll return — but "more" needs a hard
ceiling or it's an unbounded cost.*

Because both search functions dedupe post-fusion (`query_with_filters` by point ID,
`query_unique_titles` by article title — since one article can have many chunks), you have to
overfetch to guarantee enough unique results survive dedup. This project uses `limit * 100` and
`limit * 280` respectively — hardcoded magic numbers, honestly documented as such — but the real
request-facing `limit` is capped (`MAX_RESULT_LIMIT = 25`), bounding the worst case to
25 × 280 = 7,000 points instead of fully unbounded.

> **Interview angle:** Good "found a real gap" story — bounding the *input* (max limit) is a
> cheap, high-value fix even when the underlying overfetch multiplier itself stays a known,
> accepted rough edge. You don't have to solve everything to materially reduce the risk.

---

## 5. Generation

### Prompt construction / context injection

*The retrieved chunks don't help unless they're assembled into a prompt that tells the model how
to use them — and how to behave when they don't answer the question.*

`build_research_prompt(contexts, query)` is the seam between retrieval and generation: it formats
retrieved chunks with enough structure that the model can distinguish "context" from "instruction"
from "the actual question," and — critically for reducing hallucination — instructs the model on
what to do when the context doesn't contain the answer, rather than leaving that undefined.

> **Interview angle:** "How do you reduce hallucination in RAG?" — grounding instructions in the
> prompt itself ("answer only from the provided context; say so if it's insufficient") is a cheap,
> high-leverage lever, distinct from and complementary to retrieval quality.

### Multi-provider LLM abstraction

*One uniform interface (`generate_*`/`stream_*`) over OpenRouter, OpenAI, and Hugging Face — swap
providers without touching call sites.*

A `MODEL_REGISTRY` maps an enum-typed `LLMProvider` to provider-specific config, and each provider
module implements the same function shape. The route/service layer never branches on provider — it
calls the uniform interface and the registry resolves specifics. Case-insensitive enum validation
means an invalid provider name is a clean 422, not a 500 five layers deep.

**Real bug fixed here:** `MODEL_REGISTRY` was once missing an `"openai"` entry entirely, and
`generate_openai` hardcoded `model="gpt-4o-mini"` instead of reading `config.primary_model` like
the sibling function did — both fixed for consistency across providers.

### Streaming responses

*Yield tokens as they arrive instead of waiting for the full completion — lower perceived latency,
at real architectural cost.*

Implemented as an `AsyncGenerator[str, None]` wrapped in a raw `StreamingResponse` (chunked plain
text, not true SSE), with plain-text protocol prefixes the frontend parses: `__model_used__:`
(identifies auto-routed model), `__error__`, `__truncated__` (hit `max_completion_tokens`).
`await asyncio.sleep(0)` between yields keeps the event loop responsive to other requests.

> **Real architectural gap in this project:** the `@opik.track` decorator on the streaming path
> only wraps the synchronous construction of the generator closure — the actual token streaming
> happens *after* that span already closed. So evaluation scores are still computed and logged for
> streamed answers, but there's no active trace left to attach them to, and they never reach the
> dashboard. A real, acknowledged, not-yet-fixed gap — good material for "what's a limitation of
> your own system?"

---

## 6. Reliability Engineering

### Retry with exponential backoff + jitter

*Retry transient failures, fail fast on permanent ones, and space retries out so a thundering herd
doesn't retry in lockstep.*

Up to 3 attempts, scoped to connection errors, timeouts, rate limits, and 5xx — genuinely
transient conditions a retry might resolve. Auth errors and bad requests fail immediately, since
retrying an invalid API key just wastes time and quota. **Jitter** (randomizing the delay
slightly) matters at scale: without it, many clients that failed at the same moment all retry at
the same moment again, recreating the load spike that caused the failure.

**Verified live, not just unit-mocked:** a simulated `RateLimitError` on attempts 1–2 recovered on
attempt 3 against the real decorated function, with the actual backoff delay observed.

> **Interview angle:** know the exact taxonomy — which errors are retried and why the rest aren't.
> "We retry everything" is a wrong answer that sounds right — retrying a 400 or 401 just burns
> time and can mask a real config bug longer.

### Timeouts on every outbound call

*Without one, a hung dependency hangs your request indefinitely — a slow provider becomes an
unbounded one.*

Before this was fixed, a real trace against an LLM provider took **103 seconds** for a single
non-streaming completion, with nothing capping it. A timeout converts "the request never returns"
into "the request fails predictably," which is strictly better even though it's still a failure —
a bounded failure is one you can retry, alert on, or fall back from; an unbounded one just ties up
a connection/thread forever.

### Fail closed vs. fail open

*When a security check is misconfigured, should the system default to "block" or "allow"? This
project draws that line deliberately, in two directions.*

**Fail closed** (the safe default): API key auth — if `API_SECURITY__API_KEY` is unset, *every*
protected request is rejected. A missing config is a loud, immediate error, never silent open
access. **The contrasting failure mode**, found in the very same codebase: `SupabaseDBSettings`/
`QdrantSettings` fields all carry non-functional *defaults* (host `"localhost"`, password
`"password"`, Qdrant URL `""`) — if a real secret is missing, there's no loud error, the code just
quietly tries to connect to the wrong thing and fails somewhere downstream, or silently no-ops.

> **Interview angle:** this exact contrast — one setting fails loudly on misconfiguration, another
> fails silently — is a genuinely good answer to "tell me about a security/reliability design
> decision," because it shows you can name *both* the good pattern and a real gap in the same
> system, not just recite the concept.

---

## 7. Evaluation & Observability

### LLM-as-judge & G-Eval

*Use a second, typically stronger LLM to score the first model's output against a rubric — because
"is this answer good" resists simple string metrics.*

Traditional NLP metrics (BLEU, ROUGE) compare token overlap and are notoriously bad proxies for
factual correctness or helpfulness. **G-Eval** instead prompts a judge LLM (here, OpenAI GPT-4o)
with a structured rubric per criterion and has it emit a score with reasoning — closer to how a
human would actually grade an answer. This project implements three *custom* G-Eval metrics
(faithfulness, coherence, completeness) plus three of Opik's *built-in* metrics (hallucination,
answer relevance, usefulness) — six total, scored concurrently.

> **Interview angle:** know the tradeoff cold — LLM judges are more nuanced than n-gram overlap but
> introduce their own cost, latency, and judge-model bias. Worth naming unprompted, since it shows
> you understand the limitation of the very technique you built.

### The 6 quality metrics, specifically

*Not one "is this good" score — six distinct failure modes, scored separately, because "good"
isn't one thing.*

| Metric | What it checks |
|---|---|
| Faithfulness | Does the answer stay true to the retrieved context (not the model's own beliefs)? |
| Coherence | Is the answer well-structured and internally consistent? |
| Completeness | Does it actually address the full question, not just part of it? |
| Hallucination | Does it state anything unsupported by the retrieved context? |
| Answer relevance | Does it actually address what was asked, vs. a tangent? |
| Usefulness | Would this genuinely help the person who asked? |

Run **fire-and-forget** as a background task — never awaited on the response path — so scoring
adds zero latency to what the user sees. Gated behind `OPIK__ENABLE_EVALUATION` and a real OpenAI
key, so it's a strict opt-in that costs nothing when off.

### Context Precision & Recall — a different axis entirely

*The 6 metrics above grade the **generation**. These two grade the **retrieval** — genuinely
separate questions.*

An answer can be faithful to bad context (garbage in, faithfully-reported garbage out) —
faithfulness alone can't catch a retrieval failure. **Context Precision** asks: of what was
retrieved, how much was actually relevant? **Context Recall** asks: of what's relevant in the
corpus, how much did retrieval actually surface? Both require a curated `expected_output` grounded
in real retrieved chunks — this project has 6 golden queries with genuine expected outputs, not
invented ones, specifically so these two metrics have something real to compare against.

> **Interview angle:** "How do you debug a bad RAG answer — is it the retriever or the
> generator?" Context precision/recall vs. the 6 generation metrics is precisely the tool for that
> diagnosis: bad context metrics point at retrieval; good context but bad generation metrics point
> at the prompt or the model.

### Golden eval datasets & regression testing

*A fixed set of representative queries, scored the same way every time, compared against a saved
baseline — so a prompt or retrieval change that quietly makes things worse gets caught before it
ships.*

Without this, "I improved the prompt" is a claim you can't actually verify — you'd need to eyeball
outputs and hope you notice regressions. Running the same golden queries through the real pipeline
after any change and diffing scores against a saved baseline turns "I think this is better" into a
number you can defend.

> **Interview angle:** this is the single most senior-sounding thing you can say about an eval
> setup: "we don't just eyeball outputs, we regression-test against a golden set." Most personal
> RAG projects skip this entirely — having it is a real differentiator.

### Distributed tracing for LLM calls

*A trace ties every step of one request — retrieval, prompt build, provider call, eval scoring —
into one inspectable timeline, instead of scattered, uncorrelated log lines.*

`@opik.track` plus `track_openai` wraps both custom functions and the OpenAI SDK client itself, so
provider-level calls show up in the same trace as application-level spans automatically.
Evaluation scores attach to the trace as **feedback scores** — visible right next to the request
that produced them on the dashboard, not in a separate disconnected report.

**Real bug fixed here:** `OPIK__WORKSPACE` defaults to `"default"` — if your real Comet/Opik
workspace has a different name, traces silently go to a workspace that either doesn't match or
doesn't exist, and nothing ever appears on your dashboard with no error thrown anywhere.
Root-caused via `OPIK: Unauthorized` console messages. A great example of a config default that's
individually reasonable but produces a totally silent failure.

---

## 8. API & Security

### Configuration management

*One typed, validated, frozen settings object as the single source of truth — never scattered
`os.getenv()` calls.*

Pydantic Settings v2, nested via `env_nested_delimiter="__"` (so `SUPABASE_DB__HOST` maps to
`settings.supabase_db.host`), read once at process startup and **frozen** — editing `.env` on an
already-running process has zero effect until restart. This buys type validation, fail-fast on
missing required values, and IDE autocomplete on every config field, at the cost of needing a
restart to pick up changes.

> **Interview angle:** "frozen settings" is a good detail to volunteer — it explains a real class
> of confusing bugs (changed .env, restarted the wrong process, config seemingly "not taking
> effect") and shows you understand your own config's lifecycle, not just that a settings class
> exists.

### Auth & rate limiting

*A shared API key gates every protected route; a per-IP limiter caps request volume — two
independent, complementary defenses.*

API key auth fails closed (covered above). Rate limiting is per-IP and **X-Forwarded-For-aware** —
meaning it reads the real client IP from that header instead of the load balancer's own IP, which
matters the moment you deploy behind any reverse proxy (this project sits behind Caddy) — without
that awareness, every request would appear to come from the proxy itself and the limiter would be
useless.

### Request-size bounds as a security boundary

*Every user-facing input has an explicit ceiling — not because normal use needs it, but because
someone eventually sends the abnormal case.*

`limit` is `ge=1, le=25` (via `MAX_RESULT_LIMIT`); `query_text` is capped at `max_length=2000`.
These aren't UX niceties — they cap the worst-case cost of the overfetch multipliers described
earlier and prevent a trivially large request from turning into a trivially large bill or a slow
query. Validation at the API boundary (not deep in a service function) means bad input never even
reaches the expensive code path.

---

## 9. Testing

### Unit vs. integration, and what to mock

*Unit tests mock every external dependency (fast, deterministic, run on every push); integration
tests hit real services (slower, real coverage, gated to manual runs).*

This project's unit suite fully mocks Qdrant and the LLM provider calls — fast, and immune to a
flaky external service failing CI for reasons unrelated to your code. Integration tests hit a
real, auto-provisioned test database, catching the class of bug mocks structurally can't: a real
SQL constraint violation, a real connection pool exhaustion, a real serialization mismatch.

**Real bug fixed here:** `tests/unit/test_fastapi.py` used to silently depend on a live Qdrant
instance and a live OpenRouter call — meaning a "unit" test suite wasn't actually unit-testable,
and a flaky network call could fail CI with no code change involved. Fixed with a fake
vectorstore fixture and a patched `generate_openrouter`.

---

## 10. Deployment & Operations

### Docker & image hygiene

*A multi-stage build separates "what it takes to build this" from "what it takes to run this" —
and a real secret must never end up baked into a pushed image layer.*

The builder stage (`uv`-based) installs and compiles dependencies; the runtime stage copies only
the resulting virtual environment and app code, producing a meaningfully smaller final image.
`.dockerignore` excluding `.env` is the concrete mechanism that keeps secrets out of any layer — a
registry (ECR) is a persistent artifact store, not an ephemeral build context, so a secret baked in
once is effectively permanent unless the whole image history is scrubbed.

**Real verification, not assumption:** confirmed via `docker history --no-trunc` on the built
image, and separately by running the container and checking `/app/.env` doesn't exist inside it —
verifying the actual artifact, not trusting that `.dockerignore` obviously worked.

### IAM & least privilege

*Every identity gets exactly the permissions its job requires — no more — and long-lived
credentials are avoided wherever a better option exists.*

Three separate identities in this deployment, each scoped tighter than the last: a personal
`deploy-cli` IAM user (broad, for hands-on setup) → a dedicated `github-actions-ci` user scoped to
*only* `AmazonEC2ContainerRegistryPowerUser` (can push/pull images, cannot touch EC2 or IAM at
all) → the EC2 instance's own **IAM instance role** (read-only ECR pull, and critically, no stored
access keys on the box at all — AWS rotates short-lived credentials automatically).

> **Interview angle:** "Why an instance role instead of just putting access keys in the EC2
> environment?" — a leaked instance is a contained blast radius (the role's narrow permissions,
> auto-rotated) vs. a leaked long-lived key (valid until manually revoked, potentially far broader
> scope).

### CI/CD as two gated pipelines

*CI proves the code is good; CD only runs if CI already said yes — a broken build should never
reach production.*

**CI** (lint, format, type-check, unit tests) runs on every push/PR. **CD** triggers via
`workflow_run` keyed to CI's completion, gated on `conclusion == 'success'` — so a failing test run
never triggers a deploy, full stop, not "deploys anyway and you find out later." CD then
builds+pushes images and SSHes into the deploy target to pull and restart.

**Real bug found here:** `frontend/Dockerfile` was still listed in `.gitignore` — a stale rule
from before it was rewritten — meaning it had *never actually been committed* despite working
locally for weeks. Would have silently broken CD's frontend build on a fresh checkout. A reminder
that "works on my machine" and "is actually in version control" are different claims, and only one
of them survives a clean CI checkout.

### Orchestration: scheduled flows vs. request-driven code

*Prefect turns a Python function into something schedulable, retryable, and observable — without
the app itself needing a cron daemon bolted on.*

`@flow`/`@task` decorators add structure (retries, run tracking, concurrent `.map()` over feeds) to
plain functions. A **deployment** is a separate config object — schedule, target infrastructure,
pull steps — layered on top of a flow; the same flow can be triggered by that schedule *or*
manually (UI button, CLI, API) at any time, independent of each other.

Two execution models matter here: a **worker** is a process *you* run continuously, polling for
scheduled work — simple, but it has to live somewhere, competing for that machine's resources 24/7
for work that might run once a week. A **Managed/serverless** pool has the orchestrator's own
infrastructure spin up ephemeral compute per run and tear it down afterward — no idle process, at
the cost of less control over the execution environment.

**Real bug found here:** deployment YAML's `job_variables` (environment variables/secrets injected
into the run) must nest *inside* `work_pool:`, not sit as a sibling key — putting it at the wrong
indentation level deployed with zero error and silently saved an empty config. Found by triggering
a real run and reading its actual failure (connected to a hardcoded default host instead of the
real one) rather than trusting a clean deploy command as proof of correctness.

> **Interview angle:** "Cron vs. a real orchestrator?" — cron gives you scheduling and nothing
> else; an orchestrator adds retries, dependency graphs between tasks, a UI to see run
> history/failures, and a manual-trigger path for free. Worth it once a pipeline has more than one
> step or anyone besides you needs to see whether it ran.

### Infrastructure cost tradeoffs

*"Free tier" has an expiration date and specific rules — treating it as permanent is how a
$0/month project quietly becomes a real bill.*

Concrete tradeoffs made in this project: App Runner has *no* free tier at all (billed from second
one) vs. a free-tier-eligible EC2 instance (750 hrs/month, effectively one instance running
continuously); an Elastic IP is free *only while attached to a running instance* — stop the
instance and the same IP starts accruing a small charge; building images on a 1GiB-RAM box is a
real risk in itself, independent of whether it could even run the result afterward, hence build
locally/in CI and only ever `pull` on the instance.

> **Interview angle:** naming a specific "free ≠ free forever" gotcha (the Elastic IP one is a
> good, non-obvious example) signals real hands-on cloud experience far more than reciting "EC2 has
> a free tier."

---

## Interview Q&A Bank

Click a question to reveal the answer. Try answering out loud first.

### Retrieval & search

<details><summary>Why hybrid search instead of dense-only?</summary>

Dense embeddings smooth away exact terms (names, acronyms, SKUs) that users often search for
verbatim. Sparse/BM25 catches exact lexical matches dense misses; dense catches
paraphrase/synonymy sparse misses. Fusing both gets better recall than either alone.
</details>

<details><summary>Why RRF instead of just averaging the two scores?</summary>

Cosine similarity (0–1, bounded) and BM25 (unbounded, corpus-dependent) aren't on comparable
scales — averaging them directly is mixing units. RRF uses only rank position within each list,
sidestepping the scale mismatch entirely.
</details>

<details><summary>If RRF already ranks results, why rerank afterward?</summary>

RRF fuses based on where each result placed in two independently-computed lists — it never
actually compares the query text against the chunk text jointly. A cross-encoder reads both
together and catches relevance signal rank fusion structurally can't see.
</details>

<details><summary>Why not run the cross-encoder over the whole corpus instead of just the top-25?</summary>

Cross-encoders score one query/document pair per forward pass — no shared precomputed embeddings
to reuse. That's accurate but far too slow at corpus scale. Cheap bi-encoder retrieval narrows the
field first; the expensive step only ever touches a small candidate set.
</details>

<details><summary>How would you paginate this search API?</summary>

This project deliberately doesn't (called out as out-of-scope for its traffic scale) — it bounds
worst-case cost via `MAX_RESULT_LIMIT` instead. A real paginated design would need a stable sort
key and cursor, since offset-based pagination over a fused, deduped, reranked result set is
fragile — the underlying candidate set can shift between pages.
</details>

### Generation & LLMs

<details><summary>How do you reduce hallucination in a RAG system?</summary>

Layered, not single-fix: retrieval quality (hybrid + rerank) gets the right context in front of
the model; prompt instructions tell it to answer only from context and say so when context is
insufficient; evaluation (faithfulness/hallucination metrics) catches what still slips through
after the fact.
</details>

<details><summary>Why build a provider abstraction instead of calling OpenAI's SDK directly?</summary>

Vendor flexibility (swap/add providers without touching call sites), resilience (one provider's
outage doesn't take down the whole app if you can fail over), and testability (mock one interface
instead of three SDKs' worth of call shapes).
</details>

<details><summary>What's the tradeoff of streaming responses?</summary>

Lower perceived latency (first token appears fast) at the cost of architectural complexity — can't
easily retry mid-stream since chunks are already shown to the user, and post-hoc processing (like
evaluation) may lose its connection back to the originating trace, exactly as happened in this
project.
</details>

<details><summary>Fine-tuning vs. RAG vs. long context — how do you choose?</summary>

Facts that change often or need citations → RAG. Style/format/behavior that should become the
model's default → fine-tuning. Small, static corpora where retrieval overhead isn't worth it →
long context. Often combined, not mutually exclusive.
</details>

### Evaluation

<details><summary>How do you evaluate a RAG system without human labelers for every query?</summary>

LLM-as-judge (G-Eval/built-in metrics) scores generation quality at scale without per-query human
review. For genuine ground truth, a small curated golden set with real expected outputs lets you
also measure retrieval quality (context precision/recall) — the human effort goes into curating
that small set once, not grading every query forever.
</details>

<details><summary>What's the difference between context precision/recall and the 6 generation metrics?</summary>

Context precision/recall grade the *retriever* — did it find the right chunks? The 6 metrics
(faithfulness, coherence, completeness, hallucination, relevance, usefulness) grade the
*generator* — given whatever was retrieved, is the answer good? An answer can be faithful to bad
context, which is exactly why both axes are needed to diagnose a bad answer.
</details>

<details><summary>Why run evaluation as fire-and-forget instead of awaiting it?</summary>

Evaluation is for observability/regression-catching, not for gating the response — awaiting it
would add judge-model latency directly onto every user-facing request for zero benefit to that
user. Background scoring gets the same signal with zero added latency.
</details>

<details><summary>What's a real limitation in your own evaluation setup?</summary>

The streaming path's evaluation scores compute and log locally but never attach to an Opik trace,
because the tracking decorator's span closes before the actual token stream runs — a known,
acknowledged, not-yet-fixed gap. Naming a real, specific limitation (not a vague "it could be
better") is a stronger answer than claiming everything is solved.
</details>

### Reliability & systems design

<details><summary>Walk me through what happens if the LLM provider is slow or down.</summary>

A timeout bounds how long the app waits (previously unbounded — a real 103-second hang was
observed and fixed). Retry with backoff+jitter handles transient failures (connection errors,
timeouts, 429s, 5xx) up to 3 attempts, skipping retry entirely for errors a retry can't fix (auth,
bad request). What's explicitly *not* implemented: cross-provider automatic failover — named as a
deliberate scope cut for a personal project's traffic level, not an oversight.
</details>

<details><summary>What does "fail closed" mean and where does this project use it?</summary>

On misconfiguration, default to the safe/restrictive behavior rather than the permissive one. API
key auth here fails closed — an unset key rejects every request rather than silently allowing all
traffic through. Contrast: Supabase/Qdrant settings fail *open* to a broken-but-non-obviously-broken
default (localhost, empty string) — a good example of the same codebase containing both patterns,
deliberately in one case, as an unaddressed gap in the other.
</details>

<details><summary>How do you make an idempotent ingestion pipeline?</summary>

Derive the record's identity deterministically from its content (here, a hash of URL + chunk
text) instead of an auto-incrementing ID or random UUID. Re-running ingestion on already-seen
content naturally produces the same ID and upserts in place — no separate "have I processed this"
tracking table needed.
</details>

### Deployment & infra

<details><summary>Why use an IAM instance role instead of storing AWS access keys on the EC2 box?</summary>

An instance role provides short-lived, auto-rotated credentials scoped to exactly what that
instance needs (here, ECR read-only) — nothing long-lived to leak. Stored access keys are a static
secret that's valid until someone remembers to revoke it, with whatever scope was originally
granted, however broad.
</details>

<details><summary>How does your CD pipeline avoid deploying broken code?</summary>

CD is triggered via `workflow_run` keyed to the CI workflow's completion, explicitly gated on
`conclusion == 'success'` at the job level — a failing CI run still fires the CD workflow's
trigger event, but every job inside it short-circuits on that condition, so nothing actually builds
or deploys.
</details>

<details><summary>Why build Docker images in CI/locally instead of on the deployment target?</summary>

The target here is a 1GiB-RAM instance — compiling dependencies (`uv sync`, native extensions) on
a resource-constrained box is a real risk in itself, separate from whether the box could even run
the result afterward. Building elsewhere and only ever `pull`ing a finished image on the target
removes that entire risk category.
</details>

<details><summary>What's a scheduling/orchestration bug you actually hit, and how did you find it?</summary>

A deployment config's environment-variable injection (`job_variables`) was nested at the wrong
level in the YAML — it deployed with no error, silently saving an empty config. Root-caused not by
re-reading the YAML harder, but by triggering a real run and reading its actual runtime failure (it
connected to a hardcoded fallback host instead of the intended one), then confirming by reading the
orchestrator's own source code for exactly which config path it read from.
</details>
