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

1. `gcloud storage buckets create gs://aval-tfstate --location=southamerica-east1 --uniform-bucket-level-access --public-access-prevention`
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
   `aval-issuer-ed25519`, `aval-merchant-es256`, `aval-yuno-webhook-ed25519`.
4. Repo variables (GitHub → Settings → Variables): `GCP_PROJECT`,
   `GCP_REGION`, `WIF_PROVIDER`, `DEPLOY_SA`. Until `WIF_PROVIDER` is set,
   all deploy workflows self-skip.

## Deploy

```bash
# infra
tofu init -backend-config=environments/dev.backend.hcl
tofu plan  -var-file=environments/dev.tfvars
tofu apply -var-file=environments/dev.tfvars

# app (after infra): GitHub Actions → deploy-dev (auto on main) / deploy-prod (manual)
```

## Decisions baked in (see aval/docs/research/2026-08-29-iac-cloudrun-analysis.md)

- **LB gestionado + Cloud Armor, no nginx**: nginx no puede adjuntar Armor;
  `ingress=internal-and-cloud-load-balancing` evita el bypass por `*.run.app`.
- **Jobs (relay/sweeper/migrations) como Cloud Run Jobs**, no lifespan loops.
- **Secrets siempre por `--set-secrets`** (nunca env vars planas).
- **Gemini por API key hoy; Vertex/ADC es el endurecimiento recomendado.**
- **Dominio propio requerido en prod**: sin DNS no hay cert/LB y las passkeys
  fallan en `*.run.app` (Public Suffix List, ADR-018).
