#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$ROOT_DIR/openclaw/openclaw.json}"
INDEX_PATH="${OPENCLAW_AGENT_INDEX_PATH:-$ROOT_DIR/machine-behaviors/agents/INDEX.json}"

fail() { echo "[fail] $*" >&2; exit 1; }
pass() { echo "[ok]   $*"; }

command -v jq >/dev/null || fail "jq is required"
[[ -f "$CONFIG_PATH" ]] || fail "OpenClaw config not found: $CONFIG_PATH"
[[ -f "$INDEX_PATH" ]] || fail "OpenClaw agent index not found: $INDEX_PATH"

jq . "$CONFIG_PATH" >/dev/null || fail "OpenClaw config is not valid JSON"
jq . "$INDEX_PATH" >/dev/null || fail "OpenClaw agent index is not valid JSON"

expected_count="$(jq -r '.total + 1' "$INDEX_PATH")"
actual_count="$(jq -r '.agents.list | length' "$CONFIG_PATH")"
[[ "$actual_count" == "$expected_count" ]] || \
  fail "OpenClaw agent count mismatch: config has $actual_count, index expects $expected_count including main"
pass "OpenClaw agent count matches index ($actual_count)"

jq -e '
  (.agents.defaults.workspace == "/home/node/.openclaw/workspace") and
  (.agents.defaults.model.primary | type == "string") and
  (.agents.defaults.model.primary | length > 0) and
  (.agents.defaults.sandbox.mode == "all") and
  (.agents.list | map(select(.id == "main" and .default == true)) | length == 1)
' "$CONFIG_PATH" >/dev/null || fail "OpenClaw defaults/main agent contract is invalid"
pass "OpenClaw default workspace, model, sandbox, and main agent are configured"

jq -e '
  .agents.list | all(.[]; .sandbox.mode == "all")
' "$CONFIG_PATH" >/dev/null || fail "OpenClaw generated agents must run with sandbox.mode=all"
pass "OpenClaw generated agents are sandboxed"

jq -e '
  .gateway.mode == "local" and
  .gateway.auth.mode == "token" and
  .gateway.port == 18789 and
  (.gateway.bind == "lan" or .gateway.bind == "loopback") and
  (.gateway.nodes.denyCommands | type == "array") and
  (.gateway.nodes.denyCommands | index("camera.snap")) and
  (.gateway.nodes.denyCommands | index("screen.record")) and
  (.gateway.nodes.denyCommands | index("sms.send"))
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
  jq -r '.agents.list[].id' "$CONFIG_PATH" | sort > /tmp/openclaw-config-agent-ids.$$.txt
  comm -23 /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
  rm -f /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
)"
[[ -z "$missing" ]] || fail "OpenClaw config is missing indexed agents: $(echo "$missing" | head -20 | tr '\n' ' ')"
pass "OpenClaw config contains every indexed machine agent"

extra="$(
  jq -r '.agents[].agentId' "$INDEX_PATH" | sort > /tmp/openclaw-index-agent-ids.$$.txt
  jq -r '.agents.list[].id' "$CONFIG_PATH" | grep -v '^main$' | sort > /tmp/openclaw-config-agent-ids.$$.txt
  comm -13 /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
  rm -f /tmp/openclaw-index-agent-ids.$$.txt /tmp/openclaw-config-agent-ids.$$.txt
)"
[[ -z "$extra" ]] || fail "OpenClaw config contains non-index agents: $(echo "$extra" | head -20 | tr '\n' ' ')"
pass "OpenClaw config has no unindexed machine agents"
