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

if [[ -z "${POSTGRES_PASSWORD:-}" || -z "${ONBOARDING_API_KEY:-}" || -z "${KEYCLOAK_ADMIN_USERNAME:-}" || -z "${KEYCLOAK_ADMIN_PASSWORD:-}" ]]; then
  echo "backend/.env must define POSTGRES_PASSWORD, ONBOARDING_API_KEY, KEYCLOAK_ADMIN_USERNAME, and KEYCLOAK_ADMIN_PASSWORD."
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

echo "Waiting for Keycloak..."
for second in {1..60}; do
  echo "  Keycloak check ${second}/60..."
  if curl --silent --fail http://127.0.0.1:8080/ >/dev/null; then
    echo "✓ Keycloak is running at http://127.0.0.1:8080"
    break
  fi
  sleep 1
done

if ! curl --silent --fail http://127.0.0.1:8080/ >/dev/null; then
  echo "✗ Keycloak did not become available. Check: docker compose logs keycloak"
  exit 1
fi

echo "Configuring the ReconQ workspace browser flow..."
docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://127.0.0.1:8080 \
  --realm master \
  --user "$KEYCLOAK_ADMIN_USERNAME" \
  --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null
if ! docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh get authentication/flows -r reconq --fields alias 2>/dev/null | grep -q 'workspace-browser'; then
  docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh create authentication/flows -r reconq \
    -s alias=workspace-browser -s providerId=basic-flow -s topLevel=true -s builtIn=false >/dev/null
fi
workspace_executions=$(docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh get authentication/flows/workspace-browser/executions -r reconq 2>/dev/null || printf '[]')
workspace_execution_id=$(printf '%s' "$workspace_executions" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(next((x["id"] for x in data if x.get("providerId")=="workspace-token"), ""))')
if [[ -z "$workspace_execution_id" ]]; then
  workspace_execution_id=$(docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh create authentication/flows/workspace-browser/executions/execution -r reconq \
    -s provider=workspace-token -i | tr -d '\r\n')
fi
docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh update authentication/flows/workspace-browser/executions -r reconq \
  --body "{\"id\":\"$workspace_execution_id\",\"requirement\":\"ALTERNATIVE\"}" >/dev/null
docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh update realms/reconq \
  -s browserFlow=workspace-browser >/dev/null
echo "✓ ReconQ workspace browser flow is active."

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
