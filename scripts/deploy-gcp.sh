#!/usr/bin/env bash
# ==============================================================================
# Aval (TryTrust) — Direct GCP Cloud Run & Cloud Build Deployment Script
# Deploys Backend (Kernel, Merchant, Yuno Sim, Web) + Database Migrations + Jobs
# ==============================================================================

set -euo pipefail

PROJECT_ID="${GCP_PROJECT:-trytrust}"
REGION="${GCP_REGION:-southamerica-east1}"
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/aval"
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo "======================================================================"
echo -e "${BLUE}🚀 Starting Aval Deployment to GCP Cloud Run${NC}"
echo -e "Project: ${YELLOW}${PROJECT_ID}${NC}"
echo -e "Region:  ${YELLOW}${REGION}${NC}"
echo -e "Tag:     ${YELLOW}${GIT_SHA}${NC}"
echo "======================================================================"

# 1. Build and push backend image using Cloud Build
echo -e "\n${BLUE}📦 [1/6] Building Backend Container Image...${NC}"
gcloud builds submit \
  --project="${PROJECT_ID}" \
  --tag="${REPO}/backend:${GIT_SHA}" \
  .

gcloud artifacts docker tags add \
  "${REPO}/backend:${GIT_SHA}" \
  "${REPO}/backend:latest" \
  --project="${PROJECT_ID}" || true

# 2. Build and push frontend image using Cloud Build
echo -e "\n${BLUE}📦 [2/6] Building Frontend Web Container Image...${NC}"
gcloud builds submit \
  --project="${PROJECT_ID}" \
  --tag="${REPO}/web:${GIT_SHA}" \
  web/

gcloud artifacts docker tags add \
  "${REPO}/web:${GIT_SHA}" \
  "${REPO}/web:latest" \
  --project="${PROJECT_ID}" || true

# 3. Execute database migrations
echo -e "\n${BLUE}🗄️  [3/6] Running Database Migrations Job...${NC}"
gcloud run jobs execute aval-dev-migrations \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait

# 4. Update Cloud Run microservices
echo -e "\n${BLUE}🚢 [4/6] Updating Cloud Run Services...${NC}"
echo "Updating aval-dev-kernel..."
gcloud run services update aval-dev-kernel \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${REPO}/backend:${GIT_SHA}" \
  --quiet

echo "Updating aval-dev-merchant..."
gcloud run services update aval-dev-merchant \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${REPO}/backend:${GIT_SHA}" \
  --quiet

echo "Updating aval-dev-yuno-sim..."
gcloud run services update aval-dev-yuno-sim \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${REPO}/backend:${GIT_SHA}" \
  --quiet

echo "Updating aval-dev-bridge..."
gcloud run services update aval-dev-bridge \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${REPO}/backend:${GIT_SHA}" \
  --quiet

echo "Updating aval-dev-web..."
gcloud run services update aval-dev-web \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${REPO}/web:${GIT_SHA}" \
  --quiet

# 5. Tick background jobs
echo -e "\n${BLUE}⚙️  [5/6] Ticking Background Jobs (Relay & Sweeper)...${NC}"
gcloud run jobs execute aval-dev-outbox-relay --project="${PROJECT_ID}" --region="${REGION}" --wait || true
gcloud run jobs execute aval-dev-sweeper --project="${PROJECT_ID}" --region="${REGION}" --wait || true

# 6. Verify Service Health
echo -e "\n${BLUE}🔍 [6/6] Verifying Live Healthchecks...${NC}"
fail=0
for svc in aval-dev-kernel aval-dev-merchant aval-dev-yuno-sim aval-dev-bridge aval-dev-web; do
  URL=$(gcloud run services describe "$svc" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')
  REV=$(gcloud run services describe "$svc" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.latestReadyRevisionName)')
  CODE=$(curl -sS -o /tmp/resp -w '%{http_code}' --connect-timeout 5 "$URL/health" 2>/dev/null || echo "ERR")
  
  if [ "$CODE" = "200" ]; then
    echo -e "  [${GREEN}OK 200${NC}] $svc -> $URL ($REV)"
  else
    echo -e "  [${RED}FAIL $CODE${NC}] $svc -> $URL ($REV)"
    fail=1
  fi
done

echo "======================================================================"
if [ "$fail" -eq 0 ]; then
  echo -e "${GREEN}✨ Deployment complete and all services are healthy!${NC}"
else
  echo -e "${RED}⚠️ Some healthchecks failed. Check logs with 'gcloud run services logs read <service>'${NC}"
fi
echo "======================================================================"
