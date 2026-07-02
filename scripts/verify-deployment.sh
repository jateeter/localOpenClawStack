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

# OpenClaw round-trip schema gate (static; fails fast before starting the stack):
# agent instances vs oc-agent.schema.json, a completion payload vs the corpus
# localai-completion-writeback.schema.json, and the PE source-mapping artifact.
SCHEMA_PY="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3 || true)"
if [[ -n "$SCHEMA_PY" && -f "$ROOT_DIR/machine-behaviors/verify_schemas.py" ]]; then
  MB_DEBUG=0 "$SCHEMA_PY" "$ROOT_DIR/machine-behaviors/verify_schemas.py" || fail "OpenClaw round-trip schema gate failed"
  pass "OpenClaw round-trip schemas verified"
else
  pass "OpenClaw schema gate skipped (machine-behaviors or python3 unavailable)"
fi

"$ROOT_DIR/scripts/verify-openclaw-config.sh"

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

audit_json="$(docker compose exec -T openclaw-gateway sh -lc \
  'chmod 700 "$HOME/.openclaw" && timeout 45s openclaw security audit --json')" || \
  fail "OpenClaw security audit did not complete successfully within 45s"
audit_critical="$(printf '%s' "$audit_json" | jq -r '.summary.critical // 0')"
audit_warn="$(printf '%s' "$audit_json" | jq -r '.summary.warn // 0')"
audit_info="$(printf '%s' "$audit_json" | jq -r '.summary.info // 0')"
[[ "$audit_critical" == "0" ]] || fail "OpenClaw security audit reported $audit_critical critical finding(s)"
pass "OpenClaw security audit passed (${audit_critical} critical, ${audit_warn} warn, ${audit_info} info)"

if [[ "${OPENCLAW_DEEP_SECURITY_AUDIT:-false}" == "true" ]]; then
  docker compose exec -T openclaw-gateway sh -lc \
    'timeout "${OPENCLAW_DEEP_SECURITY_AUDIT_TIMEOUT:-180}s" openclaw security audit --deep' || \
    fail "OpenClaw deep security audit did not complete successfully"
  pass "OpenClaw deep security audit completed"
else
  pass "OpenClaw deep security audit skipped (set OPENCLAW_DEEP_SECURITY_AUDIT=true to run)"
fi
