#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ ! -f backend/.env ]]; then
  echo "Missing backend/.env"
  exit 1
fi

set -a
source backend/.env
set +a

if [[ -z "${POSTGRES_PASSWORD:-}" || -z "${ONBOARDING_API_KEY:-}" ]]; then
  echo "backend/.env must define POSTGRES_PASSWORD and ONBOARDING_API_KEY."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not available in PATH."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is not available. Install Docker Compose v2."
  exit 1
fi

echo "Starting the Onboarding Platform..."
# Clear stale containers/network references before Compose recreates the stack.
docker compose down --remove-orphans >/dev/null 2>&1 || true
docker compose build --no-cache
docker compose up --force-recreate --remove-orphans -d

echo "Waiting for the subscription scheduler..."
for _ in {1..30}; do
  if docker compose logs --no-color --tail=100 subscription-worker 2>/dev/null | grep -q "Subscription scheduler started"; then
    echo "✓ Subscription scheduler worker started (automatic background processing)."
    break
  fi
  sleep 1
done

if ! docker compose logs --no-color --tail=100 subscription-worker 2>/dev/null | grep -q "Subscription scheduler started"; then
  echo "⚠ Subscription scheduler worker start message not found. Check: docker compose logs subscription-worker"
fi

echo "Onboarding Platform is running at http://localhost:3001"
echo "Dashboard remote is running at http://localhost:5001"
echo "Customers remote is running at http://localhost:5002"
echo "View logs with: docker compose logs -f"
