# Devlog — IaC · Deployment infrastructure (Cloud Run, CI/CD, LLM keys)

Mission: one command from GitHub push to a hardened Google Cloud Run stack —
LB + Cloud Armor, Postgres, KMS evidence key, witness bucket, secrets,
Scheduler jobs, tracing. Protocol: newest first, every PR.

---

## 2026-08-30 — `main` integrado + promoción segura de una sola revisión
- **Why:** `main@1c5af4e` contiene la reparación de búsqueda real de Rappi,
  pero su deploy dev falló: el pipeline construía la imagen nueva y ejecutaba
  el job de migraciones todavía fijado a la imagen anterior. El workflow prod
  además actualizaba solo el kernel y usaba `latest`, por lo que podía mezclar
  revisiones entre cuatro servicios.
- **Done:** merge limpio de `origin/main` en `iac/cloudrun`; dev y prod ahora
  fijan migrations/relay/sweeper al mismo SHA del backend antes de migrar,
  abortan antes de tocar tráfico cuando la migración falla y promueven kernel,
  merchant, yuno-sim y web como una sola revisión. Prod comprueba que ambas
  imágenes existen y hace smoke de las cuatro rutas del LB. OpenTofu conserva
  la forma de los recursos e ignora únicamente el campo de imagen, cuyo dueño
  es el pipeline de promoción.
- **Release guard:** CI vuelve a ejecutar los 42 invariantes del agente sin
  clave LLM; los workflows prod exigen el kill switch
  `PROD_DEPLOY_ENABLED=true`, que solo se arma después de crear el environment
  `prod` con required reviewers, y rechazan cualquier tag que no sea un SHA
  completo de 40 caracteres.
- **Wiring fixed:** `YUNO_ISSUER_URL` ya no añade `/api` a la URL directa de
  Cloud Run en dev; en prod, yuno y merchant usan las rutas del LB que sí
  reescriben `/api`, `/yuno` y `/merchant`. `prod.tfvars` apunta al proyecto
  real `trytrust`, a imágenes existentes y a `https://trytrust.lat/yuno`; el
  estado prod comparte `trytrust-tfstate` bajo el prefijo aislado `env/prod`.
- **Rappi topology B wired:** IaC crea el secreto separado
  `aval-{env}-rappi-bridge-token`; el kernel recibe ese bearer por Secret
  Manager y la URL efímera por la variable de environment
  `RAPPI_BRIDGE_URL`. El deploy prod, cuando la URL está armada, ejecuta una
  búsqueda de chat real y exige IDs nativos `rappi_*`; un fixture `ofr_*` no
  puede hacer pasar el smoke. La sesión `ft.` nunca sale de la máquina.
- **Credential incident contained:** un plan dev reveló que Telegram había
  sido configurado manualmente como dos env vars planas; el diff de OpenTofu
  imprimió sus valores en logs públicos. Se eliminaron exactamente los tres
  runs afectados y el comentario de PR afectado. Plan/apply ahora silencian el
  detalle y publican solo direcciones + acciones; ejecuciones concurrentes se
  serializan. IaC mueve bot token y webhook secret a Secret Manager y el
  próximo deploy elimina las variables planas. Ambos valores siguen
  requiriendo rotación en su proveedor: borrar logs no revoca credenciales.
- **Verification:** Ruff limpio; 411 pytest pasan (2 GCP excluidos) sobre una
  base PostgreSQL creada desde la cadena Alembic; invariantes del agente 42/42
  sin clave LLM; build Vite/TypeScript limpio; `tofu fmt` y `tofu validate`
  verdes; YAML parsea y `docs-guard` pasa.
- **Release gates still open:** CR-02 continúa abierto (`src/api/deps.py`
  compone compras, evidencia e idempotencia con memoria); el environment
  GitHub `prod` todavía debe tener required reviewers; la clave Gemini expuesta
  durante diagnóstico y las dos credenciales Telegram expuestas deben rotarse.
  Rappi real no se sube a Cloud Run por la decisión 0030: requiere un túnel
  autenticado hacia la máquina que custodia la sesión; sin él, producción cae
  explícitamente al fixture.
- **Decision:** ninguna nueva; se aplican decisiones 0029/0030 y el playbook
  existente.
- **Contracts touched:** none.

---

## 2026-08-29 — deployment playbook + DSN dialect fix
- **Why:** the team needs the exact path from zero to deployed-with-CI/CD;
  verification while writing it exposed a real bug in the IaC wiring.
- **Bug fixed:** `AVAL_DATABASE_URL` (kernel async engine, needs
  `postgresql+asyncpg://`) and `DATABASE_URL` (psycopg / alembic / jobs, needs
  `postgresql://`) both pointed at the same secret. Split into
  `aval-{env}-db-url` (psycopg) and `aval-{env}-db-url-async` (asyncpg);
  added sensitive bootstrap outputs `db_password`, `db_dsn_psycopg`,
  `db_dsn_asyncpg` (unix-socket DSNs ready to pipe into Secret Manager).
- **Done:** [`../../../deploy/PLAYBOOK-CLOUDRUN.md`](../../../deploy/PLAYBOOK-CLOUDRUN.md) —
  8 phases: project → state bucket → first local apply (creates the SAs CI
  will later assume) → real secrets (DSNs from tofu outputs, PEMs via
  `trustlib.jose.generate_pem_pair`, LLM keys) → WIF pool/provider/binding +
  repo variables + prod environment with reviewers → app pipeline (merge,
  auto deploy, migrations-first, smoke) → domain/LB/Armor hardening
  (ingress via LB only, passkeys need the real domain) → post-deploy
  checklist, ops table (rollback, rotations) and troubleshooting. `tofu
  validate` green after the split.
- **Decision:** none new.
- **Contracts touched:** none.

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
