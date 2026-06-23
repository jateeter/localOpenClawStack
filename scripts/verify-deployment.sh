#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=/dev/null
set -a; source .env; set +a

fail() { echo "[fail] $*" >&2; exit 1; }
pass() { echo "[ok]   $*"; }

[[ "$OPEN_WEBUI_IMAGE" == *@sha256:* ]] || fail "Open WebUI image is not digest-pinned"
[[ "$BROWSER_IMAGE" == *@sha256:* ]] || fail "Browser image is not digest-pinned"
[[ "$NODE_IMAGE" == *@sha256:* ]] || fail "Node base image is not digest-pinned"
pass "Base and external images are pinned by immutable digest"

docker compose config --quiet
pass "Compose configuration is valid"

for service in openclaw-gateway open-webui browser; do
  cid="$(docker compose ps -q "$service")"
  [[ -n "$cid" ]] || fail "$service is not running"
  running_id="$(docker inspect -f '{{.Image}}' "$cid")"
  configured_ref="$(docker inspect -f '{{.Config.Image}}' "$cid")"
  expected_id="$(docker image inspect -f '{{.Id}}' "$configured_ref")"
  [[ "$running_id" == "$expected_id" ]] || fail "$service is not running its configured image"
  pass "$service image identity matches ($running_id)"
done

actual_version="$(docker compose exec -T openclaw-gateway openclaw --version | tr -d '\r')"
[[ "$actual_version" == *"$OPENCLAW_VERSION"* ]] || fail "OpenClaw version mismatch: $actual_version"
pass "OpenClaw runtime is $OPENCLAW_VERSION"

curl --fail --silent --max-time 10 "http://127.0.0.1:${OPENCLAW_GATEWAY_PORT:-18789}/healthz" >/dev/null
curl --fail --silent --max-time 10 "http://127.0.0.1:${OPEN_WEBUI_PORT:-8080}/health" >/dev/null
pass "Gateway and WebUI health endpoints are reachable on loopback"

status="$(curl --silent --max-time 10 --output /dev/null --write-out '%{http_code}' \
  -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" \
  "http://127.0.0.1:${OPENCLAW_GATEWAY_PORT:-18789}/v1/models")"
[[ "$status" == "200" ]] || fail "Gateway API authentication returned HTTP $status"
pass "Gateway API key authentication succeeds"

login_payload="$(jq -nc \
  --arg email "$WEBUI_ADMIN_EMAIL" \
  --arg password "$WEBUI_ADMIN_PASSWORD" \
  '{email: $email, password: $password}')"
login_status="$(curl --silent --max-time 10 --output /dev/null --write-out '%{http_code}' \
  -H 'Content-Type: application/json' \
  --data "$login_payload" \
  "http://127.0.0.1:${OPEN_WEBUI_PORT:-8080}/api/v1/auths/signin")"
[[ "$login_status" == "200" ]] || fail "WebUI admin sign-in returned HTTP $login_status"
pass "WebUI admin credentials authenticate successfully"

docker compose exec -T openclaw-gateway openclaw security audit --deep
