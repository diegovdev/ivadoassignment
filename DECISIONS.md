# Decisions

Every non-obvious technical and process choice made in this project, including decisions made interactively during the build.

---

## Language & runtime: Python 3.12

Python is the natural fit for data-engineering + ML tasks. 3.12 is the current stable release with significant performance improvements over 3.11 and first-class support in all dependencies used here.

---

## Package manager: uv

`uv` replaces pip + pip-tools + venv with a single fast tool written in Rust. Lock files are reproducible (`uv.lock`), install times are 10–100× faster than pip, and the `src/` layout works out of the box. The `[dependency-groups]` feature keeps dev-only tools out of the application image.

---

## Project layout: src-layout

`src/museums/` prevents the common mistake of importing the local directory instead of the installed package during tests. It is the modern Python packaging standard endorsed by PyPA.

---

## Data source: Wikipedia MediaWiki REST API + Wikidata SPARQL

The exercise requires Wikipedia APIs. MediaWiki REST (`/api/rest_v1/page/html/…`) returns rendered HTML that `pandas.read_html()` parses without custom HTML wrangling. City populations come from Wikidata SPARQL — the structured-data backbone Wikipedia uses internally. Both are free, no API key, consistent licensing.

**Known limitation**: city-name matching is exact-string. "New York" vs "New York City" results in `null` population for that museum. Documented as a future improvement.

---

## Database: SQLite (dev/preview) → PostgreSQL (staging/prod)

SQLite requires zero infrastructure — no credentials, no sidecar, no migrations tool for ~30 rows. Docker named volume makes it persistent locally.

SQLAlchemy 2.0 is the ORM layer for both dialects. Switching to PostgreSQL is a one-line change to `DATABASE_URL` — the ORM code is identical. `settings.effective_database_url` assembles the URL from individual env vars (`DB_HOST`, `DB_PASSWORD`, etc.) when running on ECS, enabling AWS Secrets Manager password injection.

---

## API: FastAPI + Uvicorn

FastAPI provides: auto OpenAPI docs at `/docs`, Pydantic-validated settings, and async request handling. Sync endpoints (like `/ingest`) run in a threadpool automatically — no event loop blocking without any extra configuration.

The API surface is intentionally minimal: `/ingest`, `/museums`, `/cities`, `/regression`. No auth, no pagination — MVP scope.

---

## ML: scikit-learn LinearRegression

The task specifies linear regression. `sklearn.linear_model.LinearRegression` is deterministic, auditable, no hidden hyperparameters. R² is returned alongside slope/intercept for communicating model quality to non-technical readers. Negative predicted visitor counts are clamped to zero (`max(0, int(pred))`).

---

## Docker: two-service Compose

`api` and `jupyter` are separated because they have different restart policies, health-check paths, and log concerns. Jupyter waits for the API health check before starting (`depends_on: condition: service_healthy`). Multi-stage Dockerfile keeps dev dependencies out of the production layer.

---

## IaC: Pulumi Python (not Terraform/CDKTF)

CDKTF (CDK for Terraform) was considered but deprecated in December 2025. Terraform HCL requires a DSL context switch. Pulumi Python allows real Python (loops, conditionals, imports) in infrastructure code, uses the same mental model as the application code, and has first-class support for AWS ECS + RDS patterns.

---

## Environments: preview / staging / prod (no separate UAT)

Three environments are sufficient:

| Env | Backend | Min tasks | RDS | Purpose |
|---|---|---|---|---|
| Preview | SQLite (ephemeral) | 1 (task, not service) | none | Per-PR rapid feedback |
| Staging | PostgreSQL | 1 | db.t3.micro, auto-shutdown 8pm–7am | Integration + UAT |
| Prod | PostgreSQL | 2 (HA across 3 AZs) | db.t3.small, multi-AZ | Production |

**No separate UAT environment**: engineers validate on staging; stakeholders do UAT on staging too. A separate UAT env would double RDS costs for identical infrastructure with no quality benefit at this scale.

---

## 3 Availability Zones for HA, 2 minimum prod tasks

Three AZs give ECS task scheduler room to place tasks across zones without the 3× task cost. Two minimum tasks spread across 3 AZs = HA: if one AZ fails, one task is still running. ECS auto-scaling (CPU-based, target 60%) handles load spikes up to the configured maximum.

---

## RDS auto-shutdown for staging (EventBridge + Lambda)

Staging RDS stops at 8pm UTC and starts at 7am UTC via a pair of EventBridge rules targeting an inline Lambda. This cuts ~11 hours/day of RDS cost in staging (~46% saving). Not applied to prod (prod is always-on by design).

---

## RDS password rotation: AWS Secrets Manager `manage_master_user_password`

`manage_master_user_password=True` on the RDS instance delegates password creation and rotation to AWS entirely — no rotation Lambda to maintain. AWS rotates every 7 days. The ECS execution role is granted `secretsmanager:GetSecretValue` to read the secret at task launch, and the password is injected via the ECS `secrets` field (not env vars, so it never appears in CloudWatch logs).

---

## Pulumi stack configs: `environments/` directory (no "Pulumi" prefix)

Pulumi's default convention names stack configs `Pulumi.<stackname>.yaml`. Because the user requested names without the "Pulumi" prefix, configs live in `infra/environments/{env}.yaml` and are specified explicitly:
```bash
pulumi up --stack staging --config-file environments/staging.yaml
```

---

## Preview deploys: ECS Fargate task with public IP (no ALB)

Per-PR preview environments use a Fargate task with `assignPublicIp=ENABLED` in a public subnet. The task public IP is posted as a PR comment. No ALB is created per-PR — ALBs are expensive (~$0.008/hour/LCU) for a temporary environment. Cold starts are acceptable (preview is for inspection, not SLA). The task stops when the PR closes.

---

## CD pipeline: staging (automatic) → prod (manual approval)

Every merge to `main` deploys automatically to staging. Deploying to prod requires a reviewer to approve the "Deploy → prod" step in GitHub Actions via a GitHub Environment protection rule. This gives stakeholders a staging window for UAT before production traffic is affected.

---

## Versioning: python-semantic-release (not Release Please)

`python-semantic-release` (PSR) reads conventional commits and automates: version bump in `pyproject.toml`, `CHANGELOG.md` generation, git tag, GitHub Release. Chosen over Release Please because:
1. PSR is Python-native (configured in `pyproject.toml`)
2. User explicitly requested semantic-release
3. PSR integrates cleanly with commitizen for commit format enforcement

---

## Conventional Commits: enforced via pre-commit + commitizen

`commitizen` provides both the pre-commit `commit-msg` hook (enforces conventional commit format locally) and the CI semantic release logic. `pre-commit` also runs `black` and `ruff` on every commit, making the formatter/linter a first-class git citizen rather than an afterthought.

---

## Git branching: simplified git flow

```
main          ← protected; production releases only; semantic-release creates tags
feature/*     ← branch from main; PR opens preview env; merge to main triggers staging→prod
hotfix/*      ← branch from main; emergency fixes; same pipeline as feature/*
```

Full Gitflow (with `develop`, `release/*`) was considered but adds overhead without benefit at this team size. The staging environment serves the role of `develop` — every merge to main lands in staging automatically for validation before prod.

---

## CI structure: preflight + PR CI + nightly (three distinct roles)

| Layer | When | Purpose |
|---|---|---|
| **Preflight** | First in every pipeline | Fail fast (< 30s) on broken lockfile, YAML syntax errors, or broken Compose config. Prevents wasting 5–10 min of CI. |
| **PR CI** | Every push to a PR | Developer feedback loop. Lint, format, unit tests, security SAST, Docker build + Trivy, Bruno integration tests. Fast and cheap. No real Wikipedia calls. |
| **Nightly** | 02:00 UTC daily | Drift detector. Full Wikipedia ingest, Locust stress test, pip-audit against live CVE feeds, Trivy on fresh images. Catches external changes (new CVE, Wikipedia table change) that don't trigger on your commits. |

---

## Stress test thresholds: error rate + p95 + p99

Both p95 and p99 latency thresholds are enforced to catch different failure modes:
- p95 catches the "most users are slow" case (widespread degradation)
- p99 catches "tail latency spikes" that affect a smaller but still significant population

Nightly thresholds are tighter (p95 < 1500ms, p99 < 2500ms) than manual stress test defaults (p95 < 2000ms, p99 < 3500ms) because nightly runs against a controlled local environment with no external network jitter.

---

## Bruno API tests: opencollection YAML format (not .bru)

Bruno supports both `.bru` (proprietary text format) and `.yml` (opencollection YAML). The YAML format was chosen because:
- Diffs are readable in GitHub PR reviews
- Standard YAML tooling applies (syntax check, schema validation)
- `bru run` supports both formats equally in CI

---

## Principal-level subagents (not skills)

Three Claude Code agents are configured in `.claude/agents/`: `code-writer`, `code-reviewer`, `test-writer`. Agents are used instead of skills because:
- Agents run with their own context window (don't pollute the main conversation)
- Agents can be invoked at principal level with specific tool restrictions (reviewer is read-only)
- Skills are for lightweight in-conversation tasks; agents suit larger, semi-autonomous workloads

---

## Matrix builds: Python 3.12 + 3.13

The `_python-check.yml` reusable workflow runs against Python 3.12 (pinned production version) and 3.13 (next stable) to catch compatibility regressions before upgrading. The matrix adds ~2 min of parallelism for near-zero marginal cost.

---

## CloudWatch alarms: 4 signals (5xx, p99 latency, ECS capacity, RDS CPU)

The four alarms cover the four most common failure modes:
1. **5xx spike** — application errors, downstream outage
2. **p99 latency** — performance regression, DB contention  
3. **ECS under-capacity** — task crash loop, OOM kill
4. **RDS CPU** — query explosion, missing index, runaway ingest

All route to an SNS topic with email subscription (configurable per stack).
