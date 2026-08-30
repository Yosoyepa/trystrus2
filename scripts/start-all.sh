#!/usr/bin/env bash
# ==============================================================================
# Aval (TryTrust) — Start All Services Orchestration Script
# NextWave Hackathon 2026 Challenge: "The buyer who isn't human" (Yuno x Nauta)
#
# Usage:
#   scripts/start-all.sh              # Auto-detects compose (Podman/Docker) or local
#   scripts/start-all.sh --compose    # Force Docker/Podman Compose container cluster
#   scripts/start-all.sh --local      # Run locally via `uv` and `npm` with PID tracking
#   scripts/start-all.sh --stop       # Stop all running containers / background processes
# ==============================================================================

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PID_FILE=".aval_services.pids"

# Detect compose tool
COMPOSE_CMD=""
if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
  COMPOSE_CMD="podman compose"
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
fi

MODE="auto"
if [ "${1:-}" = "--compose" ]; then
  MODE="compose"
elif [ "${1:-}" = "--local" ]; then
  MODE="local"
elif [ "${1:-}" = "--stop" ] || [ "${1:-}" = "--down" ]; then
  MODE="stop"
fi

stop_services() {
  echo "==> Stopping Aval services..."
  if [ -n "$COMPOSE_CMD" ]; then
    $COMPOSE_CMD down --remove-orphans 2>/dev/null || true
  fi

  if [ -f "$PID_FILE" ]; then
    while read -r pid; do
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "--> Terminating process $pid"
        kill "$pid" 2>/dev/null || true
      fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi
  echo "==> All Aval services stopped."
}

if [ "$MODE" = "stop" ]; then
  stop_services
  exit 0
fi

if [ "$MODE" = "auto" ]; then
  if [ -n "$COMPOSE_CMD" ]; then
    MODE="compose"
  else
    MODE="local"
  fi
fi

if [ "$MODE" = "compose" ]; then
  if [ -z "$COMPOSE_CMD" ]; then
    echo "ERROR: Neither 'podman compose' nor 'docker compose' was found. Use --local." >&2
    exit 1
  fi

  echo "======================================================================"
  echo "🚀 Launching Aval (TryTrust) Microservices Cluster via $COMPOSE_CMD"
  echo "======================================================================"
  
  $COMPOSE_CMD up -d --build

  echo
  echo "Waiting for services to become healthy..."
  ./scripts/smoke-test.sh || {
    echo "⚠️ Smoke tests did not pass immediately; checking logs:"
    $COMPOSE_CMD ps
  }

  echo
  echo "======================================================================"
  echo "✅ Aval Cluster Running:"
  echo "   - Web App & Reverse Proxy: http://localhost:3000"
  echo "   - Kernel Trust Layer:      http://localhost:8001"
  echo "   - Yuno AP2 Sim Rail:       http://localhost:8002"
  echo "   - VuelaYa Merchant:        http://localhost:8003"
  echo "   - PostgreSQL Database:     localhost:5432 (User: aval, DB: aval)"
  echo "======================================================================"
  echo "Stop services with: scripts/start-all.sh --stop"
  exit 0
fi

if [ "$MODE" = "local" ]; then
  echo "======================================================================"
  echo "🚀 Launching Aval Services locally in background (Host mode)"
  echo "======================================================================"

  stop_services

  # Create directories
  mkdir -p secrets var

  # Ensure DB is up if docker is present
  if [ -n "$COMPOSE_CMD" ]; then
    echo "--> Starting Postgres container..."
    $COMPOSE_CMD up -d db
  fi

  export PYTHONPATH="src:."
  export AVAL_DATABASE_URL="${AVAL_DATABASE_URL:-postgresql+asyncpg://aval:aval@localhost:5432/aval}"
  export YUNO_DATABASE_URL="${YUNO_DATABASE_URL:-postgresql+asyncpg://aval:aval@localhost:5432/aval}"
  export MERCHANT_DATABASE_URL="${MERCHANT_DATABASE_URL:-postgresql+asyncpg://aval:aval@localhost:5432/aval}"

  echo "--> Starting Kernel service (:8001)..."
  uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 > var/kernel.log 2>&1 &
  echo $! >> "$PID_FILE"

  echo "--> Starting Yuno AP2 Simulated Rail (:8002)..."
  uv run uvicorn src.yuno_sim.main:app --host 0.0.0.0 --port 8002 > var/yuno_sim.log 2>&1 &
  echo $! >> "$PID_FILE"

  echo "--> Starting VuelaYa Merchant service (:8003)..."
  uv run uvicorn src.merchant.main:app --host 0.0.0.0 --port 8003 > var/merchant.log 2>&1 &
  echo $! >> "$PID_FILE"

  echo "--> Starting React Frontend SPA (:3000)..."
  npm --prefix web run dev -- --port 3000 --host 0.0.0.0 > var/web.log 2>&1 &
  echo $! >> "$PID_FILE"

  echo
  echo "PIDs saved to $PID_FILE. Testing endpoints..."
  sleep 2
  ./scripts/smoke-test.sh || true

  echo
  echo "======================================================================"
  echo "✅ Aval Services Running in Background:"
  echo "   - Web App & Console:       http://localhost:3000"
  echo "   - Kernel Trust Layer:      http://localhost:8001"
  echo "   - Yuno AP2 Sim Rail:       http://localhost:8002"
  echo "   - VuelaYa Merchant:        http://localhost:8003"
  echo "======================================================================"
  echo "To view logs: tail -f var/*.log"
  echo "To stop all:  scripts/start-all.sh --stop"
fi
