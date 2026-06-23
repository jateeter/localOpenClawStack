#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  # shellcheck source=/dev/null
  set -a; source .env; set +a
fi

mkdir -p openclaw openwebui-data browser-config
OWNER="${LOCAL_OWNER:-$(id -un)}"
GROUP="${LOCAL_GROUP:-$(id -gn)}"

if [[ -f openclaw/openclaw.json ]]; then
  tmp="$(mktemp)"
  port="${OPENCLAW_GATEWAY_PORT:-18789}"
  jq --arg localhost_origin "http://localhost:${port}" \
     --arg loopback_origin "http://127.0.0.1:${port}" '
    .gateway.mode = "local" |
    .gateway.bind = "lan" |
    .gateway.auth.mode = "token" |
    del(.gateway.auth.token, .gateway.controlUi.allowInsecureAuth,
        .gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback,
        .gateway.controlUi.dangerouslyDisableDeviceAuth) |
    .gateway.auth.rateLimit = {
      maxAttempts: 10,
      windowMs: 60000,
      lockoutMs: 300000,
      exemptLoopback: false
    } |
    .gateway.controlUi.allowedOrigins = [
      $localhost_origin,
      $loopback_origin
    ]
  ' openclaw/openclaw.json > "$tmp"
  mv "$tmp" openclaw/openclaw.json
fi

chown "$OWNER:$GROUP" .env
chmod 600 .env
for path in openclaw openwebui-data browser-config; do
  chown -R "$OWNER:$GROUP" "$path"
  find "$path" -type d -exec chmod 700 {} +
  find "$path" -type f -exec chmod 600 {} +
done

# This tracked documentation file lives beside ignored runtime state.
[[ -f openclaw/claude.md ]] && chmod 644 openclaw/claude.md
