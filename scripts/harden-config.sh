#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  # shellcheck source=/dev/null
  set -a; source .env; set +a
fi
# shellcheck source=lib/oc-config.sh
source "$ROOT_DIR/scripts/lib/oc-config.sh"

# A native gateway names its own state directory (OPENCLAW_STATE_DIR); the
# container stack's directories live in this repo.
mkdir -p "$OC_DIR"
if [[ -z "${OPENCLAW_STATE_DIR:-}" ]]; then mkdir -p openwebui-data browser-config; fi
OWNER="${LOCAL_OWNER:-$(id -un)}"
GROUP="${LOCAL_GROUP:-$(id -gn)}"
SCHEMA="$(oc_schema "$OC_CONFIG")"

if [[ ! -f "$OC_CONFIG" ]]; then
  jq -n \
    --arg model "${OPENCLAW_DEFAULT_MODEL:-ollama/nemotron-3-nano:4b}" \
    --arg schema "$SCHEMA" '
    {
      gateway: {},
      agents: {
        defaults: {
          model: {primary: $model},
          models: {},
          sandbox: {mode: "all"}
        }
      }
    } | if $schema == "entries" then .agents.entries = {} else .agents.list = [] end
    ' > "$OC_CONFIG"
fi

tmp="$(mktemp)"
port="${OPENCLAW_GATEWAY_PORT:-18789}"
# The container gateway listens on the LAN side of its network; the native one is
# on the loopback of the Mac and containers reach it through host.docker.internal.
bind="${OPENCLAW_GATEWAY_BIND:-lan}"
# The OpenAI-compatible /v1 API. The container image is told by environment
# variables in its compose service; a native gateway has only this file.
http_endpoints="${OPENCLAW_HTTP_ENDPOINTS:-false}"
model="$(jq -r '.agents.defaults.model.primary // "ollama/nemotron-3-nano:4b"' "$OC_CONFIG")"
ollama_base="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
ollama_model="${model#ollama/}"   # provider model id without the "ollama/" prefix
jq --arg localhost_origin "http://localhost:${port}" \
   --arg loopback_origin "http://127.0.0.1:${port}" \
   --argjson gwport "$port" \
   --arg bind "$bind" \
   --arg schema "$SCHEMA" \
   --argjson http_endpoints "$http_endpoints" \
   --arg ollama_base "$ollama_base" \
   --arg ollama_model "$ollama_model" \
   --arg model "$model" '
    .gateway.mode = "local" |
    .gateway.bind = $bind |
    .gateway.auth.mode = "token" |
    .gateway.port = $gwport |
    .gateway.nodes = (.gateway.nodes // {}) |
    (["camera.snap", "screen.record", "sms.send"]) as $denied |
    if $schema == "entries" then
      .gateway.nodes.commands.deny = (((.gateway.nodes.commands.deny // []) + $denied) | unique) |
      del(.gateway.nodes.denyCommands)
    else
      .gateway.nodes.denyCommands = (((.gateway.nodes.denyCommands // []) + $denied) | unique)
    end |
    (if $http_endpoints then .gateway.http.endpoints.chatCompletions.enabled = true else . end) |
    .agents = (.agents // {}) |
    .agents.defaults = (.agents.defaults // {}) |
    .agents.defaults.sandbox = ((.agents.defaults.sandbox // {}) + {mode: "all"}) |
    # OpenClaw >= 2026.10 reads the model allow-list from modelPolicy.allow (it
    # migrated defaults.models into it), and wants a fleet to name its owner for
    # heartbeat, system and Talk work: the main agent.
    (if $schema == "entries" then
       .agents.defaults.modelPolicy.allow =
         (((.agents.defaults.modelPolicy.allow // []) + [$model]) | unique) |
       .agents.ownership = "explicit" |
       .agents.defaults.heartbeat.agentId = "main" |
       .agents.defaults.systemAgent.agentId = "main" |
       .talk.agentId = "main"
     else . end) |
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
  ' "$OC_CONFIG" > "$tmp"
mv "$tmp" "$OC_CONFIG"

if [[ -f .env ]]; then
  chown "$OWNER:$GROUP" .env
  chmod 600 .env
fi
if [[ -z "${OPENCLAW_STATE_DIR:-}" ]]; then
  paths=("$OC_DIR" openwebui-data browser-config)
else
  paths=("$OC_DIR")
fi
for path in "${paths[@]}"; do
  chown -R "$OWNER:$GROUP" "$path"
  find "$path" -type d -exec chmod 700 {} +
  find "$path" -type f -exec chmod 600 {} +
done

# This tracked documentation file lives beside ignored runtime state.
# The name is CLAUDE.md, uppercase (the engineering contract's naming rule): on a
# case-sensitive filesystem (Linux, a container) the lowercase test never
# matched, and as the script's last command it failed the script.
if [[ -f "$OC_DIR/CLAUDE.md" ]]; then chmod 644 "$OC_DIR/CLAUDE.md"; fi
