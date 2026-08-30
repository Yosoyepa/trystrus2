# Playbook — Despliegue de Aval en Google Cloud Run + CI/CD (GitHub ↔ GCP)

Guía operativa paso a paso, de cero a stack desplegado con pipeline. Todo el
IaC vive en [`iac/`](../iac/) (OpenTofu, ya validado: `tofu validate` OK) y
los workflows en [`.github/workflows/`](../.github/workflows/). El análisis
completo está en
[`aval/docs/research/2026-08-29-iac-cloudrun-analysis.md`](../aval/docs/research/2026-08-29-iac-cloudrun-analysis.md).

Arquitectura resultante: **GitHub Actions (WIF, sin claves JSON) → Artifact
Registry → Cloud Run** (kernel, yuno-sim, merchant, web) con **Global LB +
Cloud Armor**, **Cloud SQL Postgres 16** (socket Unix), **Secret Manager**,
**KMS EC_SIGN_ED25519**, **GCS witness versionado**, **Cloud Scheduler →
Jobs** (relay/sweeper/migraciones) y alertas (p95/5xx/SQL CPU).

---

## 0. Precondiciones y variables

Herramientas locales: `gcloud` (CLI), `tofu` 1.11.x, `uv`, `gh` (opcional).
Acceso: rol **Owner** (o equivalentes: `resourcemanager.projectCreator` +
billing `billing.user`) en la organización.

Fija estas variables una sola vez y reemplázalas en todos los comandos:

```bash
export OWNER_REPO="bysergr/trytrust-backend"        # dueño/repo de GitHub
export PROJECT_ID="aval-demo-001"                   # id del proyecto GCP (global, único)
export REGION="southamerica-east1"
export DOMAIN="trytrust.lat"                        # vacío en dev si aún no hay DNS
```

### Gate de código (NO desplegar sin esto)

| Gate | Qué | Dueño |
|---|---|---|
| CR-01 | Unificar el DDL de `mandates` (kernel `aval/contracts/fixtures/schema.sql` vs `src/agent/db.py.SCHEMA`/alembic-0001). Si se despliega sin esto, quien provisiona primero gana y el otro carril devuelve 503. | Dev 2 + Dev 3 |
| CR-02 | Componer el kernel con los adaptadores Postgres/KMS/GCS en `deps.py` (hoy usa stores en memoria: multi-instancia pierde compras/evidencia/idempotencia). | Dev 2 |
| Rotación | El `.env` local contiene una clave Gemini real — **rotarla** antes de cualquier demo o compartición. | Equipo |

Para un `dev` humo de infraestructura se puede aplicar sin los gates, pero el
pipeline de app no será fiable hasta cerrarlos.

---

## Fase 1 — Proyecto GCP

```bash
gcloud projects create "$PROJECT_ID" --name="Aval TryTrust"
gcloud billing projects link "$PROJECT_ID" --billing-account=BILLING_ID
gcloud config set project "$PROJECT_ID"
```

Las ~14 APIs las habilita el propio `tofu apply` (archivo `iac/apis.tf`).
Verificación: `gcloud services list --enabled | head`.

---

## Fase 2 — Bucket de estado (fuera del estado)

```bash
gcloud storage buckets create "gs://aval-tfstate" \
  --location="$REGION" --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update gs://aval-tfstate --versioning
```

---

## Fase 3 — Primer apply de infraestructura (local, con tus credenciales)

El primer apply corre **local** porque crea las service accounts que el CI
usará después (incluida `aval-dev-deploy`, a la que luego se le ata WIF).

```bash
cd iac
gcloud auth application-default login

tofu init -backend-config=environments/dev.backend.hcl
tofu plan  -var-file=environments/dev.tfvars -input=false
tofu apply -var-file=environments/dev.tfvars -input=false
```

Crea: APIs, Artifact Registry `aval`, Cloud SQL PG16, KMS
`EC_SIGN_ED25519`, bucket witness, los 7 secretos (placeholders), las SAs
(runtime/jobs/deploy) con mínimos privilegios, 4 servicios Cloud Run y 3 jobs.

`tofu plan` tarda; en dev (`domain=""`) no se crea LB ni cert — los servicios
quedan en URLs `*.run.app` para verificar todo barato primero.

---

## Fase 4 — Secretos reales

### 4.1 DSNs de base de datos (del estado de tofu → Secret Manager)

```bash
cd iac
tofu output -raw db_dsn_psycopg  | gcloud secrets versions add aval-dev-db-url       --data-file=-   # psycopg / alembic / jobs
tofu output -raw db_dsn_asyncpg  | gcloud secrets versions add aval-dev-db-url-async --data-file=-   # kernel async engine
```

Ojo al dialecto: `AVAL_DATABASE_URL` exige `postgresql+asyncpg://…` y
`DATABASE_URL` exige `postgresql://…` — por eso son **dos secretos**.

### 4.2 Claves de firma (PEMs) — con la propia trustlib del repo

```bash
python - <<'PY'
from src.trustlib.jose import generate_pem_pair
import pathlib
for name, curve in (("aval-issuer-ed25519", "Ed25519"),
                    ("aval-merchant-es256", "P-256"),
                    ("aval-yuno-webhook-ed25519", "Ed25519")):
    pem, _ = generate_pem_pair(curve=curve)
    pathlib.Path(f"/tmp/{name}.pem").write_bytes(pem)
    print("generated", name)
PY
for s in aval-issuer-ed25519 aval-merchant-es256 aval-yuno-webhook-ed25519; do
  gcloud secrets versions add "$s" --data-file="/tmp/$s.pem"
done
```

### 4.3 Idempotencia y claves LLM

```bash
openssl rand -hex 32 | gcloud secrets versions add aval-dev-idem-secret --data-file=-
# Gemini (ai.google.dev) y OpenAI (platform.openai.com) — claves POR ENTORNO:
echo -n "GEMINI_KEY"  | gcloud secrets versions add aval-dev-llm-gemini-key --data-file=-
echo -n "OPENAI_KEY"  | gcloud secrets versions add aval-dev-llm-openai-key --data-file=-
```

Verificación: `gcloud secrets versions list <secret> | head -3` — ninguna
versión debe ser `REPLACE_ME`.

---

## Fase 5 — Conectar GitHub a GCP (Workload Identity Federation, sin claves)

### 5.1 Pool y proveedor OIDC

```bash
gcloud iam workload-identity-pools create aval-github --location=global
gcloud iam workload-identity-pools providers create-oidc github \
  --location=global \
  --workload-identity-pool=aval-github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${OWNER_REPO}'"
```

### 5.2 Atar la SA de deploy al repo (mínimo privilegio por condición)

```bash
export DEPLOY_SA="aval-dev-deploy@${PROJECT_ID}.iam.gserviceaccount.com"
export POOL_NUMBER=$(gcloud iam workload-identity-pools describe aval-github \
  --location=global --format 'value(name)')

gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_NUMBER}/attribute.repository/${OWNER_REPO}"
```

### 5.3 Variables del repo en GitHub

```bash
gh variable set GCP_PROJECT   --body "$PROJECT_ID"
gh variable set GCP_REGION    --body "$REGION"
gh variable set WIF_PROVIDER  --body "projects/$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')/locations/global/workloadIdentityPools/aval-github/providers/github"
gh variable set DEPLOY_SA     --body "$DEPLOY_SA"
```

Mientras `WIF_PROVIDER` no exista, todos los workflows de deploy **se
auto-saltan** (safe gate ya implementado). Environment `prod` con
**required reviewers**: GitHub → Settings → Environments → New environment
`prod` → Required reviewers.

### 5.4 Si prefieres dar permisos a nivel proyecto al SA de deploy

Ya lo hace `iac/iam.tf` (`roles/run.admin`, `cloudsql.admin`, etc.). Para el
endurecimiento post-hackathon: mover esos bindings a recursos concretos
(condición IAM sobre el bucket/keyring) en vez de proyecto completo.

---

## Fase 6 — Pipeline de aplicación (el deploy de la app)

1. **Merge de esta rama**: PR `iac/cloudrun` → `main` (el `docs-guard` corre
   solo). Al mergear, `deploy-dev.yml` se dispara automáticamente.
2. Primer deploy manual (recomendado para ver los logs en vivo):
   Actions → **deploy-dev** → Run workflow.
3. El pipeline hace, en orden: build+push de `backend` y `web` → **job de
   migraciones** (`alembic upgrade head`, `--wait`: si falla, NO despliega) →
   deploy del kernel con secretos y Cloud SQL por socket → smoke
   `curl /healthz | grep ok` → ejecución única de relay y sweeper.

Verificación:

```bash
gcloud run services list                        # 4 servicios aval-dev-*
KERNEL_URL=$(gcloud run services describe aval-dev-kernel \
  --region "$REGION" --format 'value(status.url)')
curl -fsS "$KERNEL_URL/healthz" && curl -fsS "$KERNEL_URL/health"
```

---

## Fase 7 — Dominio, LB y endurecimiento (prod)

1. **DNS**: toma la IP del LB y crea el registro A.

   ```bash
   cd iac
   # prod.tfvars ya trae domain="trytrust.lat" → el apply crea LB + cert
   tofu apply -var-file=environments/prod.tfvars -input=false
   tofu output -raw lb_ip   # → registro A de trytrust.lat (y de api., si se usa)
   ```

2. El certificado gestionado tarda **15–60 min** en emitirse tras propagar el
   DNS: `gcloud compute ssl-certificates describe aval-prod-cert`.
3. El `url_map` ya replica el contrato del nginx local: `/api/*` → kernel
   (strip de prefijo), `/yuno/*`, `/merchant/*`, default → SPA.
4. **Endurecimiento** (ya en el código, solo aplica al cambiar a prod):
   kernel/yuno/merchant con `ingress=internal-and-cloud-load-balancing` —
   nadie bypasea el Armor por la URL `*.run.app` —, Cloud Armor en `preview`
   la primera semana (revisar logs de falsos positivos) y luego quitar
   `preview` para exigir las reglas.
5. **Passkeys**: solo funcionan con el dominio real (`*.run.app` está en la
   Public Suffix List). Probar registro de passkey en `https://trytrust.lat`
   antes del demo.

---

## Fase 8 — Verificación post-despliegue (checklist del demo)

```bash
BASE="https://${DOMAIN}"          # o la KERNEL_URL de dev
curl -fsS "$BASE/api/healthz" | grep ok
curl -fsS "$BASE/api/audit/verify" -X POST | jq .ok      # cadena íntegra
curl -fsS "$BASE/api/agent/limits" | jq                   # agente vivo
```

- [ ] `/healthz` y `/health` OK vía LB (con WAF delante).
- [ ] Migraciones aplicadas: `gcloud run jobs execute aval-migrations
      --region "$REGION" --wait` no falla y `alembic current` coincide.
- [ ] Scheduler verde: `gcloud scheduler jobs list --location "$REGION"` y
      logs de `aval-dev-outbox-relay` con `outbox.drain.complete`.
- [ ] KMS + witness: crear una compra → `gsutil ls
      gs://aval-prod-witness/roots/` tras un checkpoint.
- [ ] Passkey: registrar y firmar un mandato en el dominio real.
- [ ] Alertas: canal de notificación conectado en
      Monitoring → Alerting (p95, 5xx, SQL CPU).
- [ ] Secretos: ninguna versión `REPLACE_ME`; ninguna clave en env var plana.

---

## Operación

| Tarea | Comando |
|---|---|
| Rollback de la app | `gcloud run services update-traffic aval-dev-kernel --to-revisions=PREV=100 --region $REGION` |
| Correr el relay a mano | `gcloud run jobs execute aval-dev-outbox-relay --region $REGION --wait` |
| Rotar un secreto | `gcloud secrets versions add <s> --data-file=-` → la siguiente revisión lo toma (los montados con `:latest` al reiniciar instancia) |
| Rotar la KMS | nueva versión de la clave + nuevo `kid` en JWKS (asimétricas no rotan solas) |
| Ver plan de IaC en PR | automático con `infra-plan.yml` (comentario en el PR) |
| Aplicar IaC | Actions → **infra-apply** → elegir `dev`/`prod` (prod pide approval) |

### Troubleshooting rápido

| Síntoma | Causa probable | Arreglo |
|---|---|---|
| `UNAUTHENTICATED` en Actions | WIF mal mapeado o condición de repo distinta | compara `attribute-condition` con `OWNER_REPO`; revisa binding `workloadIdentityUser` |
| Deploy sube pero `/healthz` falla | el contenedor no escucha en `$PORT` o crashea en arranque | `gcloud run services logs read aval-dev-kernel --region $REGION`; usualmente falta un secreto o CR-01 |
| `relation "mandates" does not exist` | migraciones no corrieron o DDL divergente (CR-01) | ejecutar job `aval-migrations`; resolver CR-01 |
| 403 en rutas normales | Cloud Armor (falso positivo) | bajar sensibilidad a 0 o excluir la regla; reglas están en `preview` al inicio |
| 429 masivo | rate-limit de Armor o cuota del proveedor LLM | revisar `rate_limit_threshold` (100/min/IP) y cuotas de Gemini/OpenAI |
| Cold start muy lento | min=0 + seed/DDL en el arranque | subir `min_instances` a 1 en prod (ya en tfvars) y resolver CR-14 |
| El LLM no responde pero el flujo funciona | fallback determinista (por diseño) | revisar que `LLM_MODEL` no sea un modelo retirado y que la clave exista |

---

## Orden resumido (TL;DR)

```bash
# 1. proyecto + estado
gcloud projects create $PROJECT_ID && gcloud billing projects link $PROJECT_ID --billing-account=BILLING_ID
gcloud storage buckets create gs://aval-tfstate --location=$REGION --uniform-bucket-level-access --public-access-prevention
# 2. infra (local, primera vez)
cd iac && gcloud auth application-default login
tofu init -backend-config=environments/dev.backend.hcl
tofu apply -var-file=environments/dev.tfvars -input=false
# 3. secretos reales (fase 4 de este playbook)
# 4. WIF + variables del repo (fase 5)
# 5. merge iac/cloudrun → main → deploy-dev automático → smoke
# 6. prod: tofu apply -var-file=environments/prod.tfvars + DNS + approval
```

Costo esperado en modo demo: **≈ $38/mes** con LB+Armor y `min=0`
(~$96/mes con `min=1` y Cloud SQL mayor). Sin LB: <$15/mes.
