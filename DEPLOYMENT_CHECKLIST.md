# AWS Deployment Checklist — Substack RAG (EC2 Free Tier)

> Working checklist for actually shipping this app to AWS. Derived from `PRODUCTION_CHECKLIST.md`
> Phase 2, expanded with full step-by-step instructions since this is a first AWS deployment.
> Check items off as they're completed; add notes/gotchas inline as they come up so this stays a
> real record of what happened, not just the plan.

**Architecture recap:** one EC2 `t2.micro`/`t3.micro` instance running three containers via
`docker-compose` — `backend` (FastAPI :8080), `frontend` (Gradio :7860), `caddy` (reverse proxy,
:80, single entry point). Images are built locally and pushed to ECR; the EC2 instance only ever
pulls and runs them, never builds. Target cost: **$0/month** on the 12-month free tier.

**Decisions locked in:**
- Region: `us-west-2` (Oregon) — matches the Qdrant instance's region (every live query hits it)
- SSH key pair: new, dedicated to this instance (created in step 10, not reused)
- IAM: dedicated IAM user (`deploy-cli`) for local CLI use, separate from root; EC2 itself uses an
  IAM **instance role** (not stored keys) to pull from ECR

---

## Phase 1 — Local code fixes ✅ DONE

- [x] `.dockerignore` excludes `.env` (verified: no secrets baked into image layers)
- [x] `src/api/main.py` — `reload` gated behind `UVICORN_RELOAD` env var, no longer hardcoded
- [x] `frontend/app.py` — single `demo.launch(...)` block, `server_name="0.0.0.0"`, `PORT` env var
- [x] `frontend/Dockerfile` created (mirrors root `Dockerfile`, `CMD ["python", "-m", "frontend.app"]`, `EXPOSE 7860`)
- [x] `docker-compose.yml` created (`backend`, `frontend`, `caddy` services)
- [x] `Caddyfile` created (path-based routing, plain HTTP, access logging, `/docs`/`/redoc`/`/openapi.json` routed to backend)
- [x] Local verification: `docker compose up --build`, confirmed `/health` through Caddy, frontend loads, real Qdrant/Supabase round-trip works end-to-end

---

## Phase 2 — ECR + EC2 (real AWS resources — you run every command yourself)

### Step 0 — AWS credentials ✅ DONE

- [x] Logged into AWS Console with root account (console only — no root access keys generated)
- [x] Created IAM user `deploy-cli` (no console password, CLI/API access only)
- [x] Attached policies: `AmazonEC2FullAccess`, `AmazonEC2ContainerRegistryFullAccess`
      (`IAMFullAccess` attached now or deferred to just before step 10 — needed to create the EC2
      instance role)
- [x] Created access key for `deploy-cli`, ran `aws configure` (region `us-west-2`, `json` output)
- [x] Verified with `aws sts get-caller-identity` — returns the IAM user's ARN, not an error

### Step 8 — Create two ECR repositories ✅ DONE

- [x] Create backend repo:
  ```
  aws ecr create-repository --repository-name substack-backend --region us-west-2
  ```
- [x] Create frontend repo:
  ```
  aws ecr create-repository --repository-name substack-frontend --region us-west-2
  ```
- [x] Note the `repositoryUri` from each response (format:
      `<account-id>.dkr.ecr.us-west-2.amazonaws.com/substack-backend`) — needed for step 9

Both created as **private** repos (the default) — correct choice, since the EC2 instance role
authenticates via IAM rather than needing public/anonymous pulls.

*What*: two empty private image storage repos, one per service.
*Why*: this is where built images live so EC2 can pull them later.
*Cost*: negligible (~500 MB/month free for 12 months, then ~$0.10/GB-month).

### Step 9 — Build, tag, and push both images to ECR ✅ DONE

- [x] Authenticate Docker to ECR:
  ```
  aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-west-2.amazonaws.com
  ```
- [ ] Build (or reuse Phase 1's local images) and tag:
  ```
  docker build -t substack-backend:local -f Dockerfile .
  docker tag substack-backend:local <account-id>.dkr.ecr.us-west-2.amazonaws.com/substack-backend:latest

  docker build -t substack-frontend:local -f frontend/Dockerfile .
  docker tag substack-frontend:local <account-id>.dkr.ecr.us-west-2.amazonaws.com/substack-frontend:latest
  ```
- [ ] Push both:
  ```
  docker push <account-id>.dkr.ecr.us-west-2.amazonaws.com/substack-backend:latest
  docker push <account-id>.dkr.ecr.us-west-2.amazonaws.com/substack-frontend:latest
  ```

*What*: upload the already-verified local images under ECR's naming format.
*Why*: build happens on your machine (real RAM/CPU) — never on the tiny EC2 instance.

### Step 10 — Launch the EC2 instance ✅ DONE

- [x] Create a new SSH key pair (dedicated to this instance):
  ```
  aws ec2 create-key-pair --key-name substack-rag-key --query 'KeyMaterial' --output text > substack-rag-key.pem
  ```
  (on Windows, protect this file — do not commit it; treat like a password)
- [x] Create an IAM role for the EC2 instance granting ECR pull access
      (attach managed policy `AmazonEC2ContainerRegistryReadOnly`) and an instance profile wrapping it
- [x] Create a security group allowing inbound:
  - port 22 (SSH) — restricted to own IP (`/32`)
  - port 80 (HTTP) — open (`0.0.0.0/0`)
- [x] Launch a `t3.micro` instance (Amazon Linux 2023), attaching the key pair, IAM instance role,
      and security group from above

*Decisions made*: region `us-west-2`, AMI = Amazon Linux 2023, default SSH user = `ec2-user`.

**Real gotcha hit here (Windows-specific):** redirecting `create-key-pair`'s output with PowerShell's
`>` operator writes UTF-16 with a BOM by default — SSH then rejects the `.pem` with "invalid format."
Fixed by re-reading and rewriting the file as plain ASCII:
```powershell
$content = Get-Content $path -Raw
[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::ASCII)
```
Lesson for next time: pipe key material through `Set-Content -Encoding ascii` (or generate it in Git
Bash) instead of the bare `>` redirection in PowerShell.

### Step 11 — Allocate an Elastic IP ✅ DONE

- [x] Allocate an Elastic IP address
- [x] Associate it with the running instance

*Why*: without one, the public IP changes on every stop/start — bad for a shareable URL. Free while
attached to a running instance.

### Step 12 — SSH in; install Docker + Compose plugin ✅ DONE

- [x] `ssh -i substack-rag-key.pem ec2-user@<elastic-ip>`
- [x] Installed Docker via `sudo dnf install -y docker` + `sudo systemctl enable --now docker`
- [x] Added `ec2-user` to the `docker` group (required a fresh SSH login to take effect)
- [x] Installed the Compose plugin manually (`~/.docker/cli-plugins/docker-compose` — AL2023's
      `dnf` package doesn't bundle it)

### Step 13 — Add a swap file (2 GB) ✅ DONE

- [x] `sudo dd if=/dev/zero of=/swapfile bs=1M count=2048`
- [x] `sudo chmod 600 /swapfile`
- [x] `sudo mkswap /swapfile && sudo swapon /swapfile`
- [x] Added `/etc/fstab` entry so it persists across reboots

*Why*: 1 GiB RAM is tight for a backend loading 3 ML models (dense embedder, sparse embedder,
reranker). Swap is cheap insurance against an OOM kill.

### Step 14 — Clone the repo onto the instance ✅ DONE

- [x] `sudo dnf install -y git`
- [x] `git clone https://github.com/Ankur-Yadav09/substack-newsletters-search-system.git`

*Why*: `docker-compose.yml` and `Caddyfile` aren't baked into any image — they're the orchestration
layer, still needed even though the images themselves come from ECR.

### Step 15 — Create a real `.env` on the instance ✅ DONE

- [x] Copied the local, already-working `.env` up via `scp` (avoids retyping long API keys by hand)
- [x] Updated deployment-specific values: `ALLOWED_ORIGINS` (added the Elastic IP), `GRADIO_AUTH_USERS`
      (set — was empty for local dev, must be set once publicly reachable)

*Note*: `BACKEND_URL` didn't need editing in `.env` itself — `docker-compose.yml` already overrides it
to `http://backend:8080` for the frontend container via its `environment:` block.

### Step 16 — Authenticate to ECR from the instance and go live ✅ DONE

- [x] `aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-west-2.amazonaws.com`
      (worked automatically via the IAM instance role — no `aws configure` needed on the instance)
- [x] `docker compose pull`
- [x] `docker compose up -d`

**Real gotcha hit here:** `docker-compose.yml`'s `image:` fields read from `BACKEND_IMAGE`/
`FRONTEND_IMAGE` env vars (falling back to a local-only tag if unset) — these aren't in
`.env.example` since they're compose-specific, not app config. Had to add them to `.env` on the
instance before `pull` would fetch the right images:
```
BACKEND_IMAGE=<account-id>.dkr.ecr.us-west-2.amazonaws.com/substack-backend:latest
FRONTEND_IMAGE=<account-id>.dkr.ecr.us-west-2.amazonaws.com/substack-frontend:latest
```

### Step 17 — End-to-end verification ✅ DONE

- [x] `curl http://<elastic-ip>/health` returns healthy
- [x] Opened `http://<elastic-ip>/` in a browser — Gradio login prompt appeared, logged in
- [x] Asked a real question through the UI, got a real answer back
- [x] Confirmed via `docker compose logs caddy` / `backend` / `frontend`

**The app is live on AWS.** 🎉

---

## Stage 2 — CD pipeline (`.github/workflows/cd.yml`) ✅ DONE

Triggered automatically once `ci.yml` finishes successfully on `main` (`workflow_run`, gated on
`conclusion == 'success'`) — builds both images fresh, pushes to ECR tagged `latest`, then SSHes
into the EC2 instance to `git pull` + `docker compose pull && up -d`. Turns the manual steps 9/16
above into something that happens automatically on every merge to `main`.

- [x] **Step A** — dedicated `github-actions-ci` IAM user, scoped to
      `AmazonEC2ContainerRegistryPowerUser` only (push/pull ECR — nothing else; separate from
      `deploy-cli`, can't touch EC2/IAM)
- [x] **Step B** — 4 GitHub repo secrets added: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
      `EC2_SSH_KEY` (full `substack-rag-key.pem` contents), `EC2_HOST` (Elastic IP)
- [x] **Step C** — `cd.yml` written: `build-and-push` job (checkout → configure AWS creds → ECR
      login → build/push both images) → `deploy` job (`needs: build-and-push`, SSH via
      `appleboy/ssh-action`, remote script re-authenticates to ECR via the EC2 instance role,
      `git pull` + `docker compose pull && up -d` + `docker image prune -f`)

**Real gotcha found and fixed here:** `frontend/Dockerfile` was still listed in `.gitignore` (a
stale rule predating Phase 1's rewrite) — it had *never actually been committed*, despite existing
locally and being used successfully in Phases 1 and 2's manual builds. Would have silently broken
`cd.yml`'s frontend build step on a clean checkout (`git pull`/GitHub Actions checkout would never
have had the file). Fixed by removing the stale `.gitignore` line (along with a dead
`frontend/requirements.txt` entry pointing at a file that no longer exists) and staging the file
for commit.

- [ ] Commit the pending changes (`.gitignore` fix, `frontend/Dockerfile`, this file,
      `.github/workflows/cd.yml`) and push to `main` to trigger the first real CD run — verify in
      the Actions tab that both jobs go green and the live site reflects the deploy.

---

## Stage 3 — Automated ingestion (Prefect Cloud) ✅ DONE

**Goal:** both `rss_ingest_flow` and the embeddings ingestion flow run without manual
`make ingest-*` commands — on a schedule *and* triggerable on demand from anywhere (Prefect
Cloud's UI "Run" button or `prefect deployment run`), not one or the other.

**Decisions made:**
- **Execution: Prefect Cloud "Managed" work pool** (serverless — Prefect's own infra runs the
  flow in a temporary container), not a self-hosted worker. Reasoning: a persistent worker would
  need to run continuously somewhere; the only candidate is the EC2 instance, which is already
  tight on 1 GiB RAM running backend+frontend+Caddy with 3 ML models loaded. Managed sidesteps
  that entirely — ingestion never touches the EC2 box, only Supabase/Qdrant directly.
- **Schedule: weekly.** `rss_ingest_flow` Sundays 3 AM UTC (`0 3 * * 0`); embeddings ingestion
  flow Sundays 6 AM UTC (`0 6 * * 0`, offset so new RSS rows have landed in Supabase first).
  A schedule doesn't preclude manual triggering — both are available on the same deployment.
- **Resolved risk**: the embeddings flow's fastembed dependency loaded fine on the Managed pool —
  confirmed via a real run's logs showing ~1.5GB RSS memory used mid-run, comfortably beyond what
  the 1GiB EC2 instance could have handled. This is concrete validation that avoiding a
  worker-on-EC2 was the right call, not just a theoretical RAM-avoidance argument.
- `.env.example`'s `PREFECT__API_KEY`/`PREFECT__WORKSPACE`/`PREFECT__API_URL` are **dead/aspirational**
  — nothing in `src/config.py` reads them. Real auth is `prefect cloud login` (writes to
  `~/.prefect/`, not `.env`).

### Step 1 — Create a Prefect Cloud account + workspace ✅ DONE

1. Go to **https://app.prefect.cloud** and sign up (email/password or GitHub/Google SSO) — free
   tier is sufficient. Verify your email if prompted.
2. On first login, create an **Account** (top-level org/billing entity) and a **Workspace** inside
   it (e.g. named `default` or `substack-rag`) — this is where the deployments below actually live.

### Step 2 — Authenticate the local CLI ✅ DONE

```
uv run prefect cloud login
```
Opens a browser tab — click **Authorize**. Terminal should print
`Authenticated with Prefect Cloud! Using workspace '<account>/<workspace>'.` If you have multiple
workspaces it may prompt you to pick one instead.

Verify:
```
uv run prefect profile ls
uv run prefect cloud workspace ls
uv run prefect config view
```
`config view` should show `PREFECT_API_URL` pointing at `https://api.prefect.cloud/...`, not blank
or `localhost`. **Gotcha hit here:** the first login attempt silently failed to persist anything —
`~/.prefect/profiles.toml` had an active profile with an empty `[profiles]` table. Root cause was
never fully pinned down (likely the browser-authorize click didn't complete); redoing the login
end-to-end and watching for the explicit success line fixed it.

### Step 3 — Create the Managed work pool ✅ DONE

```
uv run prefect work-pool create substack-managed-pool --type prefect:managed
```
`prefect:managed` = Prefect Cloud's own serverless execution — no worker process to run yourself.
**Gotcha:** if this errors with `Unknown work pool type 'prefect:managed'. Please choose from
azure-container-instance, cloud-run, ..., process, ...` and a log line about a "temporary server,"
the CLI isn't actually talking to Prefect Cloud (falls back to an ephemeral local server) — means
step 2 didn't really authenticate. Fix step 2 first, then retry this.

Verify:
```
uv run prefect work-pool ls
```

### Step 4 — Store Supabase/Qdrant credentials as Prefect Cloud Secret blocks ✅ DONE

**Why needed:** the Managed container has no `.env` file. Worse, every Supabase/Qdrant setting in
`src/config.py` has a silent fallback default (`host` → `"localhost"`, `password` → `"password"`,
Qdrant `url`/`api_key` → `""`) — if a secret is missing, the flow doesn't error loudly, it just
quietly tries to connect to the wrong thing.

**Only set the values that actually differ from the code's defaults** — compare your real `.env`
against `src/config.py`'s `SupabaseDBSettings`/`QdrantSettings` classes first. For this project that
turned out to be 5 values, not all 9 possible fields (`SUPABASE_DB__NAME`, `_PORT`, `_TABLE_NAME`,
and `QDRANT__COLLECTION_NAME` all matched the code defaults already, so they were left unset).

In the browser: workspace → **Blocks** (left sidebar) → **"+ Add Block"** → search **"Secret"**
(not "String", not "JSON" — those don't encrypt the value) → block name (lowercase + dashes only,
no underscores) → paste the **raw value with no surrounding quotes** (`.env`'s `"..."` quoting is
stripped by Python's dotenv loader automatically; Prefect's block field has no such parser, so
pasting the literal quote characters breaks the value) → Save. Repeat for each:

```
supabase-db-host
supabase-db-user
supabase-db-password
qdrant-url
qdrant-api-key
```

### Step 5 — Write `prefect-cloud.yaml` ✅ DONE

See the file itself at the repo root for the current content. Structure, summarized:
```yaml
pull: &pull_steps
  - prefect.deployments.steps.git_clone:
      id: pull_step
      repository: https://github.com/Ankur-Yadav09/substack-newsletters-search-system.git
      branch: main
  - prefect.deployments.steps.run_shell_script:
      id: install_step
      script: pip install .
      directory: "{{ pull_step.directory }}"

deployments:
  - name: rss-ingest-weekly
    entrypoint: src/pipelines/flows/rss_ingestion_flow.py:rss_ingest_flow
    work_pool:
      name: substack-managed-pool
      job_variables:               # <-- MUST be nested inside work_pool, see gotcha below
        env:
          SUPABASE_DB__HOST: "{{ prefect.blocks.secret.supabase-db-host }}"
          SUPABASE_DB__USER: "{{ prefect.blocks.secret.supabase-db-user }}"
          SUPABASE_DB__PASSWORD: "{{ prefect.blocks.secret.supabase-db-password }}"
    schedule:
      cron: "0 3 * * 0"
      timezone: "UTC"
    pull: *pull_steps
  # ...second deployment (embeddings-ingest-weekly) mirrors this, plus QDRANT__URL/QDRANT__API_KEY
```
No `requirements.txt` needed — `pyproject.toml` uses a standard `hatchling` build backend, so
`pip install .` inside the pull step installs the project and all its dependencies directly.

**Critical gotcha (cost real debugging time) — `job_variables` must nest inside `work_pool:`, not
sit as a sibling key next to it.** Putting it at the same indentation level as `work_pool:`/
`schedule:`/`pull:` deploys with **zero error or warning**, silently saving `job_variables: {}`.
Confirmed by reading Prefect's own source (`prefect/cli/deploy/_config.py`, which reads
`deploy_config["work_pool"]["job_variables"]`) after a triggered run failed connecting to
`localhost` instead of the real Supabase host — the tell that the env vars never actually reached
the container. Always verify after deploying with:
```
uv run prefect deployment inspect 'rss_ingest_flow/rss-ingest-weekly'
```
and confirm `job_variables` actually shows your real keys, not `{}`. **This prints the real secret
values to your terminal** — don't paste that output anywhere, and treat it as a reason to rotate
those credentials afterward if it ever lands somewhere it shouldn't (e.g. a shared chat log).

### Step 6 — Deploy ✅ DONE

```
uv run prefect deploy --prefect-file prefect-cloud.yaml --all
```
`--all` is required — a bare `prefect deploy --prefect-file ...` errors with "no name was given"
when the file defines more than one deployment. (The `make deploy-cloud-flows` Makefile target
itself still runs the bare form without `--all` — either update it or just run the command above
directly.)

**Windows-specific cosmetic gotcha:** the CLI's own Rich-based console output can crash with
`UnicodeEncodeError` on Windows' legacy `cp1252` console codepage (e.g. right after
`prefect deployment run`, trying to print an emoji). This does **not** mean the run failed — the
run is still created and executes normally; only the local pretty-printing crashes. Work around by
setting these before any `prefect` command:
```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
```
(bash/Git Bash: `export PYTHONUTF8=1` / `export PYTHONIOENCODING=utf-8`)

### Step 7 — How to actually run/test it ✅ VERIFIED

**Trigger a run right now, anytime** (identical code path to what the schedule uses):
- Browser: workspace → **Deployments** → click the deployment → **"Run"** button (top right)
- Terminal:
  ```
  uv run prefect deployment run 'rss_ingest_flow/rss-ingest-weekly'
  uv run prefect deployment run 'qdrant_ingest_flow/embeddings-ingest-weekly'
  ```

**Check a run's outcome:**
```
uv run prefect flow-run ls --limit 5 --flow-name rss_ingest_flow
uv run prefect flow-run logs <flow-run-id>
```
(Get the id from the `ls` output, or click into the run in the browser for live logs.)

**Confirming the automatic weekly schedule actually fires unattended** (not the same as a run
working when manually triggered) — check back after a Sunday without triggering anything yourself:
```
uv run prefect flow-run ls --limit 5 --flow-name rss_ingest_flow -o json
```
Look for a `Completed` run with `created_by.type == "SCHEDULE"` (not `"USER"`) timestamped that
Sunday. To get faster feedback than waiting a week, temporarily point the cron at a few minutes in
the future, redeploy, confirm it fires on its own, then restore the real weekly cron and redeploy
again.

**Real-world proof, not just "no error":** verified via actual logs, not just a clean exit —
`rss_ingest_flow` ingested 3 real new articles into Supabase (`The Neural Maze` feed);
`qdrant_ingest_flow` embedded and upserted 29 real new chunks into Qdrant, with process memory
peaking ~1.5GB RSS mid-run (comfortably beyond the 1GiB EC2 instance's capacity — concrete
validation that Managed was the right call over a worker-on-EC2).

**Other real bugs hit during this stage** (the `job_variables` nesting bug and the Windows
console-encoding crash are covered inline in steps 5/6 above — not repeated here):
1. **"tenant/user not found" red herring** — after fixing the `job_variables` nesting bug, the
   first real run still failed connecting to the *correct* real Supabase host. Root cause was
   unrelated to Prefect entirely: the Supabase project itself was paused (free-tier auto-pause).
   Resumed it in the Supabase dashboard and the identical run succeeded immediately after. Worth
   checking first if a real, previously-working connection string suddenly starts failing.
2. Three real secrets ended up printed in plaintext in this working session at different points
   (a Prefect API key via `cat profiles.toml`, a Qdrant API key via an IDE file-selection event,
   and both the Supabase password and Qdrant API key via `prefect deployment inspect`'s output —
   see step 5's warning above). None were committed to git or left in a file, but all three
   **should be rotated** out of caution given they appeared in a transcript — not yet confirmed
   done as of this writing.

---

## Deferred decision (parked, not forgotten)

**EC2 long-term cost strategy** — free-tier EC2 hours aren't forever. Options discussed, not yet
decided: (a) accept ~$7–9/month after free tier ends, (b) migrate to Oracle Cloud's "Always Free"
tier (permanent $0, more RAM headroom, but a real migration effort), (c) stop the instance when
not actively demoing it and start it back up on demand (near-$0, disk/setup persists across
stop/start since *stopping* ≠ *terminating*; only real gotcha is the Elastic IP accruing a small
idle charge while stopped, and `cd.yml`'s `EC2_HOST` secret needing an update if the IP is ever
released and reallocated). Revisit later — not blocking any current work.

---

## Explicitly deferred (not part of this checklist)

- Real domain + automatic HTTPS — just a `Caddyfile` + DNS change whenever wanted, no code changes
- Splitting `pyproject.toml` into per-service dependency groups to slim the frontend image
