# Análisis de despliegue — Aval en Google Cloud Run (IaC + CI/CD + LLM)

**Método:** 4 auditorías/investigaciones paralelas (2026-08-29, `main @ b9896d1`):
(1) desplegabilidad del repo, (2) arquitectura Cloud Run 2026 (con fuentes
oficiales), (3) integración OpenAI + Gemini, (4) diseño IaC + CI/CD.
Verificación local: OpenTofu 1.11.5 instalado; `tofu init` con provider
`hashicorp/google ~> 7.45` funciona (probado en /tmp); no hay
terraform/docker/gcloud locales.

## 1. Estado desplegable actual (síntesis)

- **Un Dockerfile** multi-stage (uv frozen, no-root, `PORT` por env respetado
  por Cloud Run, healthcheck `/health`) sirve kernel (:8001), yuno_sim (:8002)
  y merchant (:8003) vía `APP_MODULE`. SPA Vite+React servida por nginx con
  reverse-proxy `/api`, `/yuno`, `/merchant` (solo compose).
- Kernel expone mandatos/passkeys, escalaciones, decisión/verificación, audit
  (incl. `POST /audit/tamper` ¡sin auth!), evidence-pack, agent_bridge.
- Lifespan ya arrastra un sweeper de escalaciones idempotente; el outbox relay
  de Dev 2 solo se drena por CLI/systemd (no en el stack compose).
- Alembic tiene 4 migraciones reales que **nadie ejecuta** (3 vías DDL
  competentes: schema.sql, `src/agent/db.py.SCHEMA`, DDL en primer request).

## 2. Brechas bloqueantes para desplegar (CR-xx)

| ID | Brecha |
|---|---|
| **CR-01** | **Dos shapes incompatibles de `mandates`** (kernel schema.sql vs `src/agent/db.py.SCHEMA`/alembic-0001): "gana quien provisiona primero" y el otro carril crashea. La unificación de b9896d1 quedó a medias. **Resolver antes del primer deploy.** |
| **CR-02** | El kernel corre con stores **en memoria** (`deps.py`): con >1 instancia o redeploy, compras/audit/evidencia/idempotencia se pierden. Los adaptadores PG+KMS+GCS existen; falta la composición. |
| CR-03 | Migraciones sin automatizar → Cloud Run Job `alembic upgrade head` previo al deploy. |
| CR-04 | Jobs de fondo sin hogar: watcher tick + relay deben ser Cloud Run Jobs + Scheduler (no lifespan: CPU throttled + N instancias duplican). |
| CR-05 | Endpoints async con I/O bloqueante (psycopg síncrono, LLM con urllib): colapsan la concurrencia de la instancia. |
| CR-06 | Sin auth en el kernel (`/audit/tamper` público). Ingress por LB + desactivar tamper en prod. |
| CR-07..CR-14 | CORS hardcodeado, PEM de desarrollo si falta config de claves, doble DSN (asyncpg vs psycopg), probes, SPA en GCP, observabilidad cero, `.env` local con **clave Gemini real (rotar antes de cualquier demo pública)**. |

## 3. Decisiones de arquitectura

1. **LB gestionado, no nginx**: Global External ALB + serverless NEG + cert
   gestionado + Cloud Armor Standard (WAF OWASP preconfigurado + rate-limit).
   nginx sidecar no aporta (Cloud Run ya termina TLS) y **no permite adjuntar
   Cloud Armor**. Crítico: `ingress=internal-and-cloud-load-balancing` para que
   nadie bypasee el Armor por la URL `*.run.app`. El `url_map` replica el
   contrato del nginx local (`/api/*`→kernel con strip de prefijo, `/yuno/*`,
   `/merchant/*`; default→SPA).
2. **Región `southamerica-east1`** (ya declarada en `deploy/README.md`):
   consistente con el repo y con la narrativa LATAM; alternativa demo más
   barata us-central1 (decisión abierta; medir con gcping).
3. **Jobs**: Cloud Scheduler (cada 60 s) → Cloud Run Jobs `outbox-relay` y
   `sweeper` + job `migrations` (alembic) ejecutado por el pipeline antes de
   cada deploy. Nada de loops en lifespan (CPU throttled / duplicación).
4. **Secrets**: Secret Manager montados con `--set-secrets` (jamás env vars
   planas como hoy en compose); volumen `:latest` para rotación sin deploy.
5. **CI/CD**: WIF (`google-github-actions/auth@v3`) sin claves JSON;
   `ci.yml` en PR (ruff+pytest con Postgres de servicio+build); `deploy-dev`
   automático en main (gateado con `vars.WIF_PROVIDER != ''` hasta bootstrap);
   `deploy-prod` manual con environment+reviewers; `infra-plan` en PR con
   comentario de plan; `infra-apply` manual por entorno.
6. **Observabilidad**: OTel (FastAPI/psycopg/httpx + propagador
   `xcloudtrace`) → Cloud Trace; logs JSON con
   `logging.googleapis.com/trace` para correlación; 3 alertas (p95, 5xx,
   backlog outbox). Cloud Profiler descartado (agente sin mantenimiento).

## 4. LLM (OpenAI + Gemini)

- **Frontera verificada en código**: el LLM solo corre en `node_perceive`
  (parse) y `node_propose` (elige un `offer_id` del catálogo); el gate es
  determinista y re-valida el precio contra el merchant en vivo; `grep` de
  llm/gemini/openai en `src/api/` = 0 hits. **El LLM jamás decide.**
- Estado: cliente HTTP propio OpenAI-compatible sobre `urllib`, una clave
  activa (precedencia LLM_API_KEY>GEMINI>OPENAI), sin retries, modelo default
  `gemini-1.5-flash` **ya retirado** (actualizar a `gemini-2.5-flash` o 3.x
  por env, nunca hardcode).
- Spec dual: `LLM_PROVIDER` explícito (`gemini|openai`), claves en Secret
  Manager (`aval-llm-gemini-key-*`, `aval-llm-openai-key-*`), retry solo en
  429/5xx/timeout (2 intentos, backoff+jitter), circuit breaker tras 5 fallos
  → fallback determinista (ya existe y es fail-closed).
- **Recomendación Gemini: vía Vertex AI con ADC** (IAM de la SA del runtime,
  sin API key que rotar, tráfico por Private Google Access); vía API-key
  aceptable en dev. OpenAI queda con API key (no hay alternativa IAM) + su
  IP allowlist si se quiere endurecer (requiere NAT con IP estática).
- Red: no se requiere NAT salvo IP-allowlist de OpenAI; allowlist de egreso
  en-proceso (`src/agent/net.py`) se mantiene como defensa en profundidad.
- Redacción: pasar `scrub_text` (ya existe) a todo texto que vaya a prompt o
  log; jamás loguear prompts íntegros.

## 5. IaC implementada en la rama `iac/cloudrun`

Árbol plano por dominios (justificado para hackathon; extraíble a módulos
1:1 después): `iac/{versions,backend,variables,apis,iam,sql,secrets,kms,
storage,services,scheduler_jobs,lb,observability,outputs}.tf` +
`environments/{dev,prod}.tfvars` + `*.backend.hcl`. Provider
`hashicorp/google ~> 7.45` (la 6.x está superada; breaking changes 6→7
documentados). Recursos: ~12 APIs, AR repo, Cloud SQL PG16 (zonal dev /
regional prod, socket Unix), KMS `EC_SIGN_ED25519` (sin rotation_period — no
aplica a asimétricas), bucket witness versionado+PPA, 5 secrets con versiones
placeholder + accessor por SA, 4 Cloud Run services + 3 jobs, 2 Scheduler con
OIDC, 4 NEGs + backends + Armor + url_map con rewrite + cert/IP condicionados
a `var.domain != ""`, alertas y budget opcional.

Notas clave: `web` escucha 3000 fijo (nginx); `PORT=8080` lo inyecta Cloud
Run; SAs separadas (runtime/jobs/deploy) con least-privilege por recurso
(`signerVerifier` sobre la key, no sobre el keyring); deletion protection solo
en prod; el bloque LB completo es condicional (sin dominio no hay cert).

## 6. Bootstrap (cuando exista el proyecto GCP)

1. Proyecto + billing + `gcloud config set project`.
2. Bucket de estado `gs://aval-tfstate` (fuera del estado).
3. WIF: pool `aval-github` + provider OIDC (condición `assertion.repository
   == OWNER/REPO`) + SA `github-deploy` + `workloadIdentityUser` binding.
4. Secrets reales: PEMs (Ed25519/P-256 con `trustlib.jose`), password de DB,
   `aval-db-url-dev`, claves LLM. Variables de repo `GCP_PROJECT`,
   `GCP_REGION`, `WIF_PROVIDER`, `DEPLOY_SA`.
5. `tofu init -backend-config=… && tofu apply -var-file=environments/dev.tfvars`.
6. Primer `deploy-dev.yml` manual → smoke `/health`.

## 7. Costos estimados (demo/mes)

LB ~$18 + Armor ~$9-11 + Cloud Run $0-10 (min 0/1) + Cloud SQL $10-47 +
Scheduler $0 (3 gratis) + KMS/Secrets/GCS/Logging <$1 → **≈$38/mes** (con LB,
min=0, db pequeño) o **≈$96/mes** (min=1, db mayor). Sin LB queda <$15.

## 8. Decisiones abiertas para el equipo

1. DDL único: ¿migra el agente a la shape del kernel (CR-01)? Bloqueante.
2. Dominio `trytrust.lat` no resuelve aún (PROPERTIES.md P2): sin DNS no hay
   cert/LB y **las passkeys fallan en `*.run.app`** (PSL). Comprar/configurar
   ya, o demo sin passkeys.
3. ¿PayPal sandbox o solo `yuno_sim` (decisión 0024)? No hay cliente PayPal real.
4. ¿`/agent/ask` síncrono (timeout 300s) o 202 + polling por `/agent/runs`?
5. ¿Desactivar el mockEngine de la SPA en prod (puede enmascarar caídas ante
   los jueces)?
6. Workspace witness privado con URLs firmadas vs público (código hoy: privado
   con PPA recomendado).
7. Rotar la clave Gemini presente en `.env` local antes del demo.

Fuentes completas de la investigación web en los reportes de los subagentes
(LB/NEG/Armor, Cloud SQL, WIF, jobs, OTel, región, precios, Gemini/OpenAI
pricing y cuotas, Direct VPC egress).
