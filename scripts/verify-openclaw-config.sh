#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=lib/oc-config.sh
source "$ROOT_DIR/scripts/lib/oc-config.sh"

CONFIG_PATH="$OC_CONFIG"
# The index this deployment loaded: start.sh hands it over, and records it as
# INDEX.deployed.json for a verifier run on its own. The full index is the
# fallback only for a stack that never recorded one. Defaulting to it failed a
# correct regression deployment as "config has 16, index expects 1323".
DEPLOYED_INDEX="$ROOT_DIR/machine-behaviors/agents/.generated/INDEX.deployed.json"
if [[ -n "${OPENCLAW_AGENT_INDEX_PATH:-}" ]]; then
  INDEX_PATH="$OPENCLAW_AGENT_INDEX_PATH"
elif [[ -f "$DEPLOYED_INDEX" ]]; then
  INDEX_PATH="$DEPLOYED_INDEX"
else
  INDEX_PATH="$ROOT_DIR/machine-behaviors/agents/INDEX.json"
fi

fail() { echo "[fail] $*" >&2; exit 1; }
pass() { echo "[ok]   $*"; }

command -v jq >/dev/null || fail "jq is required"
[[ -f "$CONFIG_PATH" ]] || fail "OpenClaw config not found: $CONFIG_PATH"
[[ -f "$INDEX_PATH" ]] || fail "OpenClaw agent index not found: $INDEX_PATH"

jq . "$CONFIG_PATH" >/dev/null || fail "OpenClaw config is not valid JSON"
jq . "$INDEX_PATH" >/dev/null || fail "OpenClaw agent index is not valid JSON"

expected_count="$(jq -r '.total + 1' "$INDEX_PATH")"
actual_count="$(jq -r "$OC_JQ_DEFS"' oc_agents | length' "$CONFIG_PATH")"
[[ "$actual_count" == "$expected_count" ]] || \
  fail "OpenClaw agent count mismatch: config has $actual_count, index expects $expected_count including main"
pass "OpenClaw agent count matches index ($actual_count)"

# `main` is the agent flagged default in the list schema; in the entries schema
# the default is the agent that agents.ownership names for heartbeat and system work.
jq -e --arg home "$OC_HOME" "$OC_JQ_DEFS"'
  (.agents.defaults.workspace == ($home + "/workspace")) and
  (.agents.defaults.model.primary | type == "string") and
  (.agents.defaults.model.primary | length > 0) and
  (.agents.defaults.sandbox.mode == "all") and
  (if ((.agents // {}) | has("entries"))
   then (.agents.ownership == "explicit" and .agents.defaults.heartbeat.agentId == "main" and
         (.agents.entries | has("main")))
   else (oc_agents | map(select(.id == "main" and .default == true)) | length == 1) end)
' "$CONFIG_PATH" >/dev/null || fail "OpenClaw defaults/main agent contract is invalid"
pass "OpenClaw default workspace, model, sandbox, and main agent are configured"

jq -e "$OC_JQ_DEFS"'
  oc_agents | all(.[]; .sandbox.mode == "all")
' "$CONFIG_PATH" >/dev/null || fail "OpenClaw generated agents must run with sandbox.mode=all"
pass "OpenClaw generated agents are sandboxed"

# The port is the one the generator was told (OPENCLAW_GATEWAY_PORT); a native
# gateway on a remapped port (RE_PORT_OPENCLAW) is not a misconfigured one.
jq -e --argjson port "${OPENCLAW_GATEWAY_PORT:-18789}" '
  .gateway.mode == "local" and
  .gateway.auth.mode == "token" and
  .gateway.port == $port and
  (.gateway.bind == "lan" or .gateway.bind == "loopback") and
  ((.gateway.nodes.commands.deny // .gateway.nodes.denyCommands) as $deny |
   ($deny | type == "array") and
   ($deny | index("camera.snap")) and
   ($deny | index("screen.record")) and
   ($deny | index("sms.send")))
' "$CONFIG_PATH" >/dev/null || fail "OpenClaw gateway security contract is invalid"
pass "OpenClaw gateway security contract is configured"

jq -e '
  (.models.providers.ollama.api == "ollama") and
  (.models.providers.ollama.baseUrl | type == "string") and
  (.models.providers.ollama.baseUrl | startswith("http")) and
  (.models.providers.ollama.models | type == "array") and
  (.models.providers.ollama.models | length >= 1)
' "$CONFIG_PATH" >/dev/null || fail "OpenClaw Ollama provider contract is invalid"
pass "OpenClaw Ollama provider contract is configured"

jq -e '
  (.agents.defaults.model.primary) as $model |
  (.tools.byProvider[$model].deny // []) as $deny |
  ($deny | index("group:web")) and ($deny | index("browser"))
' "$CONFIG_PATH" >/dev/null || fail "OpenClaw small-model tool deny contract is invalid"
pass "OpenClaw small-model web/browser tool deny is configured"

missing="$(
  jq -r '.agents[].agentId' "$INDEX_PATH" | sort > /tmp/openclaw-index-agent-ids.$$.txt
  jq -r "$OC_JQ_DEFS"' oc_agents[].id' "$CONFIG_PATH" | sort > /tmp/openclaw-config-agent-ids.$$.txt
  comm -23 /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
  rm -f /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
)"
[[ -z "$missing" ]] || fail "OpenClaw config is missing indexed agents: $(echo "$missing" | head -20 | tr '\n' ' ')"
pass "OpenClaw config contains every indexed machine agent"

extra="$(
  jq -r '.agents[].agentId' "$INDEX_PATH" | sort > /tmp/openclaw-index-agent-ids.$$.txt
  jq -r "$OC_JQ_DEFS"' oc_agents[].id' "$CONFIG_PATH" | grep -v '^main$' | sort > /tmp/openclaw-config-agent-ids.$$.txt
  comm -13 /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
  rm -f /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
)"
[[ -z "$extra" ]] || fail "OpenClaw config contains non-index agents: $(echo "$extra" | head -20 | tr '\n' ' ')"
pass "OpenClaw config has no unindexed machine agents"
