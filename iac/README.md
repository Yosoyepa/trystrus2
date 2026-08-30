# Aval on Google Cloud Run — IaC (OpenTofu)

Flat root by domain (extractable to modules later). Branch: `iac/cloudrun`,
cut from `main` (the integrated deployment line).

```
iac/
├── versions.tf         # OpenTofu ~> 1.11, provider hashicorp/google ~> 7.45, GCS backend
├── variables.tf        # project/region/env/images/domain/scaling/db/budget
├── apis.tf             # ~14 project services (disable_on_destroy=false)
├── iam.tf              # 3 SAs (runtime, jobs, deploy) + least-privilege bindings
├── sql.tf              # Cloud SQL Postgres 16 (zonal dev / regional prod, unix socket)
├── secrets.tf          # Secret Manager: PEMs, db-url, idem-secret, LLM keys (placeholders)
├── kms.tf              # EC_SIGN_ED25519 evidence key (AVAL_KMS_KEY_RESOURCE)
├── storage.tf          # witness bucket: versioned, UBLA, PPA enforced
├── services.tf         # Artifact Registry + 4 Cloud Run services + 3 jobs
├── scheduler_jobs.tf   # 2 Scheduler jobs (60s) → relay/sweeper via OIDC
├── lb.tf               # 4 serverless NEGs + backends + Cloud Armor (OWASP+rate limit)
│                       # + url_map (/api,/yuno,/merchant strip prefix; default SPA)
│                       # + managed cert & IP (only when var.domain != "")
├── observability.tf    # alerts: p95 >5s, 5xx burst, SQL CPU >80%; optional budget
├── outputs.tf
└── environments/{dev,prod}.tfvars + {dev,prod}.backend.hcl
```

## Local validation (no GCP needed)

```bash
tofu fmt -recursive
tofu init -backend=false
tofu validate
```

## Bootstrap (once, with a GCP project)

1. `gcloud storage buckets create gs://trytrust-tfstate --location=southamerica-east1 --uniform-bucket-level-access --public-access-prevention`
2. WIF for GitHub Actions:
   ```bash
   gcloud iam workload-identity-pools create aval-github --location=global
   gcloud iam workload-identity-pools providers create-oidc github \
     --location=global --workload-identity-pool=aval-github \
     --issuer-uri="https://token.actions.githubusercontent.com" \
     --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
     --attribute-condition="assertion.repository=='OWNER/REPO'"
   # bind roles/iam.workloadIdentityUser on the deploy SA to
   # principalSet://…/attribute.repository/OWNER/REPO
   ```
3. Real secrets: `gcloud secrets versions add <secret> --data-file=-` for
   `*-db-url`, `*-idem-secret`, `*-llm-gemini-key`, `*-llm-openai-key`,
   `*-rappi-bridge-token`, `*-telegram-bot-token`,
   `*-telegram-webhook-secret`; dev keeps the existing
   `aval-{issuer-ed25519,merchant-es256,yuno-webhook-ed25519}` IDs and prod
   uses independent `aval-prod-*` signing-key IDs.
4. Repo variables (GitHub → Settings → Variables): `GCP_PROJECT`,
   `GCP_REGION`, `WIF_PROVIDER`, `DEPLOY_SA`. After configuring required
   reviewers on the `prod` environment, set `PROD_DEPLOY_ENABLED=true`.
   Set the ephemeral topology-B tunnel as the environment variable
   `RAPPI_BRIDGE_URL`; its bearer lives only in Secret Manager as
   `aval-prod-rappi-bridge-token`.
   Production's LB base is `https://api.trytrust.lat`; the apex remains on
   Vercel. Set `PROD_BASE_URL=https://api.trytrust.lat` in the protected
   environment and `KERNEL_API_URL=https://api.trytrust.lat/api` in Vercel.
   Until WIF exists, deploy workflows self-skip; until the explicit prod
   switch exists, both prod apply and prod promotion fail before selecting the
   GitHub environment.

## Deploy

```bash
# infra
tofu init -backend-config=environments/dev.backend.hcl
tofu plan  -var-file=environments/dev.tfvars
tofu apply -var-file=environments/dev.tfvars

# prod uses its own state; dev remains the sole owner of project APIs/repo
tofu init -reconfigure -backend-config=environments/prod.backend.hcl
tofu plan -var-file=environments/prod.tfvars

# app (after infra): GitHub Actions → deploy-dev (auto on main/iac) /
# deploy-prod (manual, exact commit SHA, protected environment)
```

## Decisions baked in (see aval/docs/research/2026-08-29-iac-cloudrun-analysis.md)

- **LB gestionado + Cloud Armor, no nginx**: nginx no puede adjuntar Armor;
  `ingress=internal-and-cloud-load-balancing` evita el bypass por `*.run.app`.
- **Jobs (relay/sweeper/migrations) como Cloud Run Jobs**, no lifespan loops.
- **Promoción separada del IaC**: OpenTofu administra la forma de servicios y
  jobs; Actions actualiza únicamente imágenes inmutables y siempre apunta los
  jobs a la revisión nueva antes de ejecutar migraciones.
- **Secrets siempre por `--set-secrets`** (nunca env vars planas).
- **Planes públicos sin valores**: CI publica únicamente la dirección de cada
  recurso y su acción. El plan completo puede contener drift secreto y nunca
  se imprime ni se comenta en un PR.
- **Un solo owner por recurso global**: con dev/prod en el mismo proyecto,
  `env/dev` administra APIs y Artifact Registry; `env/prod` los consume y
  administra solo recursos `aval-prod-*` (decisión 0031). Las claves de firma
  sí son independientes por ambiente.
- **Gemini por API key hoy; Vertex/ADC es el endurecimiento recomendado.**
- **Dominio propio requerido en prod**: sin DNS no hay cert/LB y las passkeys
  fallan en `*.run.app` (Public Suffix List, ADR-018). El cert del backend usa
  `api.trytrust.lat`; RP ID/origen siguen siendo el apex servido por Vercel.
- **Rappi no se hospeda en Cloud Run**: por decisión 0030, la sesión vive en
  la máquina propietaria. Producción necesita un túnel autenticado desde el
  kernel hacia ese bridge. Cuando `RAPPI_BRIDGE_URL` está armado,
  `deploy-prod` exige una búsqueda con IDs nativos `rappi_*`; sin él, el agente
  usa el catálogo fixture y no se presenta como búsqueda real.
