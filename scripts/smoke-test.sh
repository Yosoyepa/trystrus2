#!/usr/bin/env bash
# ==============================================================================
# Aval (TryTrust) — Automated Smoke Test & Healthcheck Suite
# Verifies all 4 HTTP services (Kernel, Yuno AP2, Merchant, Web SPA)
# ==============================================================================

set -euo pipefail

MAX_RETRIES=${SMOKE_RETRIES:-20}
RETRY_DELAY=${SMOKE_DELAY:-1}

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "======================================================================"
echo -e "${BLUE}🔍 Running Aval Service Cluster Smoke Tests...${NC}"
echo "======================================================================"

check_endpoint() {
  local name="$1"
  local url="$2"
  local expected_status="${3:-200}"
  local attempts=0
  local status_code=""

  printf "%-35s " "Checking $name ($url)..."

  while [ "$attempts" -lt "$MAX_RETRIES" ]; do
    status_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 "$url" || echo "000")
    if [ "$status_code" -eq "$expected_status" ]; then
      echo -e "[ ${GREEN}HTTP $status_code OK${NC} ]"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep "$RETRY_DELAY"
  done

  echo -e "[ ${RED}FAILED (Got $status_code, Expected $expected_status)${NC} ]"
  return 1
}

failures=0

echo "--- 1. Direct Service Healthchecks ---"
check_endpoint "Kernel Service (:8001)" "http://localhost:8001/health" 200 || failures=$((failures + 1))
check_endpoint "Yuno AP2 Rail (:8002)"   "http://localhost:8002/health" 200 || failures=$((failures + 1))
check_endpoint "Merchant Service (:8003)" "http://localhost:8003/health" 200 || failures=$((failures + 1))
check_endpoint "Frontend Web SPA (:3000)" "http://localhost:3000/" 200 || failures=$((failures + 1))

echo
echo "--- 2. Reverse Proxy Routing (through :3000) ---"
check_endpoint "Proxy -> Kernel (/api/health)" "http://localhost:3000/api/health" 200 || failures=$((failures + 1))
check_endpoint "Proxy -> Yuno (/yuno/health)"   "http://localhost:3000/yuno/health" 200 || failures=$((failures + 1))
check_endpoint "Proxy -> Merchant (/merchant/health)" "http://localhost:3000/merchant/health" 200 || failures=$((failures + 1))

echo
echo "======================================================================"
if [ "$failures" -eq 0 ]; then
  echo -e "${GREEN}🎉 ALL SMOKE TESTS PASSED! Cluster is fully operational.${NC}"
  echo "======================================================================"
  exit 0
else
  echo -e "${RED}❌ SMOKE TESTS FAILED: $failures endpoint(s) unreachable.${NC}"
  echo "======================================================================"
  exit 1
fi
