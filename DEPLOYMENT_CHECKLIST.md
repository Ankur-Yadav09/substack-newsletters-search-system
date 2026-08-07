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

## Explicitly deferred (not part of this checklist)

- Real domain + automatic HTTPS — just a `Caddyfile` + DNS change whenever wanted, no code changes
- Splitting `pyproject.toml` into per-service dependency groups to slim the frontend image
- Fully automated data ingestion (Prefect Cloud schedule + worker)
