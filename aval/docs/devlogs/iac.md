# Devlog — IaC · Deployment infrastructure (Cloud Run, CI/CD, LLM keys)

Mission: one command from GitHub push to a hardened Google Cloud Run stack —
LB + Cloud Armor, Postgres, KMS evidence key, witness bucket, secrets,
Scheduler jobs, tracing. Protocol: newest first, every PR.

---

## 2026-08-29 — workstream opened: analysis (4 agents) + `iac/cloudrun` scaffold
- **Why:** the team integrated all lanes on `main` (kernel + yuno_sim +
  merchant + SPA + Gemini chat); deployment to Cloud Run with IaC, LB,
  observability and GitHub CI/CD is the next milestone.
- **Analysis:** 4 parallel read-only audits — deployability, Cloud Run
  architecture 2026 (web-verified), OpenAI+Gemini integration, IaC/CI-CD
  design. Synthesis:
  [`../research/2026-08-29-iac-cloudrun-analysis.md`](../research/2026-08-29-iac-cloudrun-analysis.md).
  Key findings: CR-01 divergent `mandates` DDL (kernel vs agent) blocks a
  clean deploy; CR-02 kernel still on in-memory stores in `deps.py`;
  LLM verified to live only in propose/perceive (decision 0016 holds);
  default `gemini-1.5-flash` is retired; `.env` holds a real Gemini key
  (rotate before any public demo).
- **Done on branch `iac/cloudrun` (from `main @ b9896d1`):**
  - `iac/` OpenTofu root: versions/variables/apis/iam/sql/secrets/kms/
    storage/services/scheduler_jobs/lb/observability/outputs +
    `environments/{dev,prod}.tfvars` + backend.hcl. Provider google ~> 7.45.
    4 Cloud Run services (kernel, yuno-sim, merchant, web), 3 jobs
    (migrations, outbox-relay, sweeper), 2 Scheduler triggers (60s, OIDC),
    Cloud SQL PG16 via unix socket, KMS EC_SIGN_ED25519, versioned witness
    bucket, 7 secrets with placeholder versions and per-SA accessors,
    LB with serverless NEGs + Cloud Armor (OWASP preconfigured + rate limit)
    + url_map replicating the nginx contract, cert/IP conditioned on
    `var.domain`, alerts (p95/5xx/SQL CPU) + optional budget.
  - `.github/workflows/`: `ci.yml` (ruff + pytest with Postgres service +
    image builds), `deploy-dev.yml` (auto on main, WIF, migrations job →
    services → smoke), `deploy-prod.yml` (manual, environment-protected),
    `infra-plan.yml` (PR plan comment), `infra-apply.yml` (manual per env).
    All GCP jobs self-skip until WIF repo vars exist.
  - `src/api/jobs/relay.py`: outbox relay job entrypoint (v0: drain + log
    sink + optional signed webhook) so `aval-outbox-relay` has a command.
  - `iac/README.md`: validation + bootstrap + decisions.
- **Decision:** none new yet (LB-vs-nginx, jobs-as-Jobs, secrets-via-mounts
  and region southamerica-east1 proposed for team ratification).
- **Contracts touched:** none.
