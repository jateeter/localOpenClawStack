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

if [[ ! -f openclaw/openclaw.json ]]; then
  jq -n \
    --arg model "${OPENCLAW_DEFAULT_MODEL:-ollama/llama3.1:8b}" \
    '{
      gateway: {},
      agents: {
        defaults: {
          model: {primary: $model},
          models: {},
          sandbox: {mode: "all"}
        },
        list: []
      }
    }' > openclaw/openclaw.json
fi

tmp="$(mktemp)"
port="${OPENCLAW_GATEWAY_PORT:-18789}"
model="$(jq -r '.agents.defaults.model.primary // "ollama/llama3.1:8b"' openclaw/openclaw.json)"
ollama_base="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
ollama_model="${model#ollama/}"   # provider model id without the "ollama/" prefix
jq --arg localhost_origin "http://localhost:${port}" \
   --arg loopback_origin "http://127.0.0.1:${port}" \
   --argjson gwport "$port" \
   --arg ollama_base "$ollama_base" \
   --arg ollama_model "$ollama_model" \
   --arg model "$model" '
    .gateway.mode = "local" |
    .gateway.bind = "lan" |
    .gateway.auth.mode = "token" |
    .gateway.port = $gwport |
    .gateway.nodes = (.gateway.nodes // {}) |
    .gateway.nodes.denyCommands =
      (((.gateway.nodes.denyCommands // []) +
        ["camera.snap", "screen.record", "sms.send"]) | unique) |
    .agents = (.agents // {}) |
    .agents.defaults = (.agents.defaults // {}) |
    .agents.defaults.sandbox = ((.agents.defaults.sandbox // {}) + {mode: "all"}) |
    .tools = (.tools // {}) |
    .tools.byProvider = (.tools.byProvider // {}) |
    .tools.byProvider[$model] = (.tools.byProvider[$model] // {}) |
    .tools.byProvider[$model].deny = (((.tools.byProvider[$model].deny // []) + ["group:web", "browser"]) | unique) |
    .models = (.models // {}) |
    .models.providers = (.models.providers // {}) |
    .models.providers.ollama = (.models.providers.ollama // {}) |
    .models.providers.ollama.api = "ollama" |
    .models.providers.ollama.baseUrl = $ollama_base |
    # OpenClaw requires model *objects* here ({id, name, ...}); appending the
    # bare id produced "models.providers.ollama.models.0: Invalid input" and
    # `unique` could not dedupe a string against the equivalent object, so the
    # invalid entry was re-added on every run and the gateway refused to boot.
    # Drop any legacy bare-string entries, then add an object only if absent.
    .models.providers.ollama.models =
      (((.models.providers.ollama.models // []) | map(select(type == "object")))
       | if any(.id == $ollama_model) then .
         else . + [{id: $ollama_model, name: $ollama_model}] end) |
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

chown "$OWNER:$GROUP" .env
chmod 600 .env
for path in openclaw openwebui-data browser-config; do
  chown -R "$OWNER:$GROUP" "$path"
  find "$path" -type d -exec chmod 700 {} +
  find "$path" -type f -exec chmod 600 {} +
done

# This tracked documentation file lives beside ignored runtime state.
[[ -f openclaw/claude.md ]] && chmod 644 openclaw/claude.md
