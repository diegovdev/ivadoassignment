# Museums Visitor Correlation

Correlates annual visitor counts of the world's most-visited museums (>2 M visitors/year) with the population of their host cities. Exposes a FastAPI REST API and a Jupyter notebook with a linear regression model.

---

## Quick start

```bash
# 1. Start all services (API on :8000, Jupyter on :8888)
docker compose up --build

# 2. Ingest data from Wikipedia + Wikidata
curl -X POST http://localhost:8000/ingest

# 3. Open the notebook
open http://localhost:8888
```

---

## Architecture

```
src/museums/
├── data/
│   ├── wikipedia.py   # MediaWiki REST API + Wikidata SPARQL → raw dicts
│   └── db.py          # SQLAlchemy models (City, Museum), engine, session factory
├── ml/
│   └── regression.py  # Pure computation → RegressionResult dataclass
├── api/
│   └── main.py        # FastAPI app — /ingest, /museums, /cities, /regression
└── settings.py        # pydantic-settings (env overrides)
```

**Data flow**: `POST /ingest` → Wikipedia fetch → DB upsert → `GET /regression` → ML compute → response.

Docker Compose runs two services:
- `api` on port **8000** — FastAPI + SQLite on a named volume
- `jupyter` on port **8888** — notebook that calls the API over the internal network

---

## Environments

| Env | DB | ECS min tasks | Notes |
|---|---|---|---|
| **Preview** | SQLite (ephemeral) | 1 task | Per-PR; spun up on PR open, torn down on close |
| **Staging** | PostgreSQL (RDS) | 1 | Auto-stop 8pm UTC, auto-start 7am UTC |
| **Prod** | PostgreSQL (RDS) | 2 (across 3 AZs) | Always-on, multi-AZ HA |

Staging doubles as UAT — engineers validate here, stakeholders do acceptance testing here. No separate UAT environment is needed at this scale.

---

## SLO / SLI

### Service Level Indicators

| Signal | Metric | Source |
|---|---|---|
| Availability | % of 1-min windows with ≥1 healthy ECS task | CloudWatch `RunningTaskCount` |
| Error rate | HTTP 5xx / total requests in 5-min window | ALB `HTTPCode_Target_5XX_Count` |
| p95 latency | 95th-percentile response time for GET endpoints | ALB `TargetResponseTime p95` |
| p99 latency | 99th-percentile response time for GET endpoints | ALB `TargetResponseTime p99` |

### Service Level Objectives

| Objective | Staging | Production |
|---|---|---|
| Availability | 99 % | 99.9 % |
| Error rate | < 1 % | < 0.1 % |
| p95 latency | < 1 000 ms | < 500 ms |
| p99 latency | < 2 000 ms | < 1 000 ms |

### CloudWatch Alarms (→ SNS email)

| Alarm | Condition | Severity |
|---|---|---|
| 5xx spike | `HTTPCode_Target_5XX_Count > 5` in two consecutive 5-min periods | 🔴 Critical |
| p99 latency | `TargetResponseTime p99 > 2s` in two consecutive 5-min periods | 🟡 Warning |
| ECS under-capacity | `RunningTaskCount < min` for 1 min | 🔴 Critical |
| RDS CPU | `CPUUtilization > 90%` in two consecutive 5-min periods | 🟡 Warning |

---

## Local development

```bash
uv sync                       # install all dependencies
uv sync --extra notebook      # include Jupyter extras

uv run uvicorn museums.api.main:app --reload

# Tests
uv run pytest                                     # all tests
uv run pytest tests/test_wikipedia.py             # single file
uv run pytest -k test_parse_visitors              # single test
uv run pytest --cov=src/museums --cov-report=term-missing

# Format + lint
uv run black src/ tests/
uv run ruff check src/ tests/

# Trigger ingestion against the local API
curl -X POST http://localhost:8000/ingest
```

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/ingest` | Fetch museums from Wikipedia and city populations from Wikidata, persist to DB. Idempotent. |
| `GET` | `/museums` | List all museums with city and visitor data. |
| `GET` | `/cities` | List all cities with population. |
| `GET` | `/regression` | Linear regression: slope, intercept, R², per-museum predictions. |

Interactive docs: `http://localhost:8000/docs`

**Example regression response**:

```json
{
  "slope": 0.0023,
  "intercept": 1500000,
  "r_squared": 0.61,
  "predictions": [
    { "city": "Paris", "population": 2161000, "visitors": 9000000, "predicted_visitors": 6470 }
  ]
}
```

---

## Testing

### Unit tests

```bash
uv run pytest
uv run pytest --cov=src/museums --cov-report=term-missing
```

### Bruno integration tests (REST)

Prerequisites: `npm install -g @usebruno/cli` and a running API.

```bash
# Against local dev server
bru run test/rest/ --env-var baseUrl=http://localhost:8000

# With JSON + HTML reports
bru run test/rest/ \
  --env-var baseUrl=http://localhost:8000 \
  --reporter-json test/rest/results.json \
  --reporter-html test/rest/results.html
```

Requests run in order (seq 1–4). Request 01 seeds `ingestedMuseums` / `ingestedCities` variables used by requests 02–04 for cross-request count assertions. Every request has a `before-request` preflight that fails immediately if `baseUrl` is not set.

### Stress tests (Locust)

```bash
uv sync --extra stress

uv run locust \
  --headless \
  --host http://localhost:8000 \
  --users 30 --spawn-rate 5 --run-time 3m \
  --locustfile tests/stress/locustfile.py \
  --csv tests/stress/run

# Assert error rate + p95 + p99
python3 tests/stress/check_results.py tests/stress/run_stats.csv 0.01 1500 2500
```

Load profile: **90 %** `ReadUser` (GET /museums, /cities, /regression) + **10 %** `IngestUser` (POST /ingest).

Thresholds enforced in CI:

| Metric | Nightly | Manual (default) |
|---|---|---|
| Error rate | < 1 % | < 1 % |
| p95 latency | < 1 500 ms | < 2 000 ms |
| p99 latency | < 2 500 ms | < 3 500 ms |

---

## CI / CD

All pipelines are composed from reusable workflow layers in `.github/workflows/`:

| Layer | File | What it does |
|---|---|---|
| Preflight | `_preflight.yml` | Lockfile sync + YAML syntax + Compose config — < 30 s |
| Python check | `_python-check.yml` | ruff + black + pytest --cov, matrix: Python 3.12 + 3.13 |
| Security | `_security.yml` | Bandit SAST + pip-audit CVEs + CodeQL |
| Docker build | `_docker-build.yml` | Buildx + Trivy CRITICAL/HIGH scan + optional ECR push |
| API tests | `_api-tests.yml` | `bru run` against a live Compose stack |
| Deploy ECS | `_deploy-ecs.yml` | `ecs update-service --force-new-deployment` + smoke test |

### Pipelines

| Pipeline | Trigger | Jobs |
|---|---|---|
| **Preview** | PR opened / pushed | Build image → run ECS Fargate task (public IP) → post URL on PR → tear down on close |
| **CI** | PR / push to `main` | preflight → python-check (matrix) + security + docker-build → bruno-tests → gate |
| **CD** | Push to `main` | preflight → python-check → docker-build + push ECR → **staging** (auto) → **prod** (approval) |
| **Release** | Push to `main` | python-semantic-release: version bump + CHANGELOG + GitHub Release + git tag |
| **Nightly** | 02:00 UTC daily | preflight → python-check + security + docker-build → bruno-tests + stress-test |
| **Stress (manual)** | `workflow_dispatch` | Locust against any URL; configurable users / duration / p95 + p99 thresholds |
| **Security (manual)** | `workflow_dispatch` | One-off SAST + pip-audit scan |

**Why three CI layers?**

| Layer | When | Role |
|---|---|---|
| **Preflight** | < 30s, first in every pipeline | Fail fast on config/YAML/lockfile errors before wasting CI minutes |
| **PR CI** | Every push to a PR | Developer feedback loop — lint, tests, security, Docker. Fast, no real API calls. |
| **Nightly** | Daily at 2am | Drift detector — full Wikipedia ingest, stress test, live CVE scan. Catches external changes. |

**Branch protection**: require only the `all-checks-pass` gate job — adding or removing individual jobs never breaks the required-checks list.

---

## Deployment (AWS)

Infrastructure is defined with **Pulumi Python** in `infra/`:

```
infra/
├── __main__.py          # ECS Fargate + RDS PostgreSQL + ALB + VPC (staging/prod)
├── preview/
│   └── __main__.py      # Lightweight preview cluster (shared across all PR envs)
├── environments/
│   ├── dev.yaml         # Dev stack config
│   ├── staging.yaml     # Staging stack config (3 AZs, auto-shutdown)
│   └── prod.yaml        # Prod stack config (3 AZs, multi-AZ RDS, 2 min tasks)
└── Pulumi.yaml
```

Resources provisioned (staging/prod):
- VPC across **3 AZs** with public + private subnets, IGW, NAT Gateway
- RDS PostgreSQL with **AWS-managed password rotation** (Secrets Manager, 7-day rotation)
- ECR repositories for `api` and `jupyter` images
- ECS Fargate cluster, task definitions, services with **CPU-based auto-scaling**
- Application Load Balancer (HTTP → ECS)
- CloudWatch log groups, **4 CloudWatch alarms** → SNS email
- EventBridge + Lambda for RDS auto-shutdown (staging only)
- IAM execution role with Secrets Manager access for DB password injection

```bash
cd infra
pip install pulumi pulumi-aws

# Staging
pulumi stack init staging
pulumi up --stack staging --config-file environments/staging.yaml

# Prod
pulumi stack init prod
pulumi up --stack prod --config-file environments/prod.yaml

# Preview (provision once, shared across all PRs)
cd preview
pulumi stack init preview
pulumi up
```

DB password is managed by AWS Secrets Manager (`manage_master_user_password=True` on RDS). ECS injects `DB_PASSWORD` directly from the secret at task launch — it never appears in logs or environment variable listings. The app assembles the full connection URL from `DB_HOST` + `DB_PASSWORD` via `settings.effective_database_url`.

---

## Git branching (simplified git flow)

```
main           ← protected; production releases only; every merge triggers staging → prod pipeline
feature/*      ← branch from main; opening a PR starts a preview environment automatically
hotfix/*       ← branch from main; emergency fixes; same pipeline as feature branches
```

Full Gitflow (`develop`, `release/*` branches) adds overhead without benefit at this team size. The staging environment serves the integration role: every merge to `main` deploys there automatically before going to prod.

### Typical workflow

```bash
# 1. Start a feature
git checkout -b feature/add-museum-rating

# 2. Work + commit (conventional commits enforced by pre-commit hook)
git commit -m "feat(api): add rating field to museum response"

# 3. Push → PR → preview deploy runs automatically
git push origin feature/add-museum-rating
gh pr create

# 4. PR CI runs (lint, tests, security, Bruno)
# Preview URL posted as PR comment

# 5. Merge PR → main
# → staging deploy (automatic)
# → prod deploy (requires approval in GitHub UI)
# → semantic-release creates version tag + CHANGELOG entry
```

---

## Conventional Commits + semantic-release

This project enforces [Conventional Commits](https://www.conventionalcommits.org/) and uses **[python-semantic-release](https://python-semantic-release.readthedocs.io/)** to automate versioning.

### Enforcement (pre-commit)

Install the pre-commit hooks once after cloning:

```bash
uv sync --group dev
uv run pre-commit install --hook-type commit-msg --hook-type pre-commit
```

On every `git commit`:
- `black` auto-formats staged Python files
- `ruff` lints and auto-fixes imports
- `commitizen` validates the commit message format (blocks non-conventional messages)

### Commit message format

```
<type>(<scope>): <short description>

[optional body]

[optional footer: BREAKING CHANGE: ...]
```

| Type | Semver bump | Example |
|---|---|---|
| `fix:` | patch | `fix(db): handle null population gracefully` |
| `feat:` | minor | `feat(api): add /health endpoint` |
| `perf:` | patch | `perf(regression): cache sklearn model between requests` |
| `feat!:` or `BREAKING CHANGE:` in footer | major | `feat!: rename /museums to /exhibits` |
| `docs:`, `chore:`, `refactor:`, `test:`, `ci:` | — (no release) | |

> **Pre-1.0 behaviour** (`0.x`): `feat:` bumps patch, breaking changes bump minor. Version graduates to `1.0.0` on the first intentional `feat!:` commit.

### How releases work

1. Merge a conventional commit to `main`.
2. `release.yml` runs `semantic-release version` — analyses commits since last tag.
3. If releasable commits exist: bumps `project.version` in `pyproject.toml`, regenerates `CHANGELOG.md`, creates a signed git tag (`v0.2.0`), creates a GitHub Release.
4. The version-bump commit triggers `cd.yml` → staging → prod deploy.

### GitHub Environments setup

Create these environments in **Settings → Environments**:

| Environment | Protection |
|---|---|
| `preview` | No restrictions (auto-deploy per PR) |
| `staging` | No restrictions (auto-deploy on main merge) |
| `prod` | **Required Reviewers** — adds a manual gate before prod deploy |

### Required secrets (repository level)

| Secret | Used by |
|---|---|
| `AWS_ACCESS_KEY_ID` | All AWS workflows |
| `AWS_SECRET_ACCESS_KEY` | All AWS workflows |
| `AWS_REGION` | All AWS workflows |
| `ECR_API_URL` | docker-build, preview |
| `ECR_JUPYTER_URL` | docker-build |
| `PREVIEW_CLUSTER` | preview.yml |
| `PREVIEW_SUBNET_ID` | preview.yml |
| `PREVIEW_SG_ID` | preview.yml |

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/museums.db` | SQLAlchemy connection URL |
| `MIN_VISITORS` | `2000000` | Minimum annual visitors to include a museum |
| `WIKIPEDIA_REST_API` | `https://en.wikipedia.org/api/rest_v1` | MediaWiki REST base URL |
| `WIKIDATA_SPARQL_URL` | `https://query.wikidata.org/sparql` | Wikidata SPARQL endpoint |

Override via `.env` file or environment at runtime. In Docker Compose, `API_URL=http://api:8000` is set automatically for the Jupyter service.

---

## Dependencies

| Package | Why |
|---|---|
| **fastapi** | REST framework with auto OpenAPI docs and Pydantic integration |
| **uvicorn** | ASGI server for FastAPI |
| **sqlalchemy** | ORM over SQLite; one-line swap to PostgreSQL via `DATABASE_URL` |
| **httpx** | Sync HTTP client for Wikipedia and Wikidata API calls |
| **pandas** | `read_html()` parses the Wikipedia museum table without manual HTML parsing |
| **scikit-learn** | `LinearRegression` — deterministic, auditable, no hidden hyperparameters |
| **pydantic-settings** | Type-safe config from env vars / `.env` file |
| **beautifulsoup4 + lxml** | HTML parser backend required by `pandas.read_html()` |
| **black** | Opinionated formatter — zero config, prevents style debate in PRs |
| **ruff** | Fast linter covering pyflakes + isort |
| **pytest + pytest-cov** | Test runner with coverage reporting |
| **bandit** | Python SAST — detects common security anti-patterns |
| **pip-audit** | CVE scanning for Python dependencies |
| **locust** | Load testing framework (optional extra: `uv sync --extra stress`) |
