#!/usr/bin/env bash
# =============================================================================
# test-native-schema.sh — the generators write a config the *native* gateway
# accepts (RealityEngine_CI#592).
#
# OpenClaw >= 2026.10 keys agents by id (agents.entries), moves gateway.nodes
# denyCommands to commands.deny, wants a model allow-list in modelPolicy.allow and
# an explicit owner, and limits an agent id to 64 characters. The container image
# is still 2026.6.x and the `list` schema. This runs the real generators in a
# scratch state directory for both schemas and checks:
#
#   1. the verifier passes on what the generators wrote (generator/validator
#      drift, the failure test-config-contract.sh guards for the list schema);
#   2. the paths in the config are the state directory's, not /home/node;
#   3. set-model.sh moves the default, the allow-list and the agents;
#   4. an agent id the new schema would reject is refused with its name;
#   5. when `openclaw` is on PATH, its own `config validate` accepts the entries
#      config. Without it that last check is SKIPPED, loudly, not passed.
#
# Nothing here touches the operator's openclaw/ directory or ~/.openclaw.
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
command -v jq >/dev/null || { echo "[FAIL] jq is required" >&2; exit 1; }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
fails=0
ok()   { echo "  ok   $1"; }
bad()  { echo "  FAIL $1"; fails=$((fails + 1)); }
check() { if "${@:2}" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

# A small deployed-corpus index: the real agents of the first regression entries.
INDEX="$T/INDEX.json"
REG="$(OPENCLAW_AGENT_PROFILE=regression "$ROOT_DIR/scripts/agent-profile.sh" regression 2>/dev/null | tail -1)"
jq '.agents |= .[0:3] | .total = 3' "$REG" > "$INDEX"
# The index's relative spec paths resolve under machine-behaviors/agents/.

run_schema() { # schema state-dir
  local schema="$1" dir="$2" home="$3"
  shift 3
  mkdir -p "$dir"
  env OPENCLAW_SCHEMA="$schema" OPENCLAW_STATE_DIR="$dir" OPENCLAW_HOME_DIR="$home" \
      OPENCLAW_GATEWAY_BIND=loopback OPENCLAW_HTTP_ENDPOINTS=true \
      OLLAMA_BASE_URL=http://127.0.0.1:11434 OPENCLAW_AGENT_INDEX_PATH="${INDEX_OVERRIDE:-$INDEX}" \
      OPENCLAW_DEFAULT_MODEL=ollama/nemotron-3-nano:4b "$@"
}

echo "native-schema generators"

# ── entries (native gateway) ─────────────────────────────────────────────────
E="$T/native"; mkdir -p "$E"
run_schema entries "$E" "$E" "$ROOT_DIR/scripts/harden-config.sh" >/dev/null
run_schema entries "$E" "$E" "$ROOT_DIR/scripts/sync-machine-agents.sh" >/dev/null
run_schema entries "$E" "$E" "$ROOT_DIR/scripts/verify-openclaw-config.sh" >/dev/null 2>&1 \
  && ok "entries: the verifier passes on the generated config" || bad "entries: the verifier rejects the generated config"
CFG="$E/openclaw.json"
check "entries: agents.entries holds main + 3 agents, no agents.list" \
  jq -e '(.agents.entries | length) == 4 and (.agents | has("list") | not) and (.agents.entries | has("main"))' "$CFG"
check "entries: gateway is on loopback, /v1 enabled, denyCommands moved" \
  jq -e '.gateway.bind == "loopback" and .gateway.http.endpoints.chatCompletions.enabled == true and (.gateway.nodes.commands.deny | index("sms.send")) and (.gateway.nodes | has("denyCommands") | not)' "$CFG"
check "entries: model allow-list and explicit owner" \
  jq -e '(.agents.defaults.modelPolicy.allow | index("ollama/nemotron-3-nano:4b")) and .agents.ownership == "explicit" and .agents.defaults.heartbeat.agentId == "main"' "$CFG"
check "entries: every workspace and agentDir is under the state directory" \
  jq -e --arg d "$E" '[.agents.entries[] | (.workspace, .agentDir)] | all(startswith($d))' "$CFG"
! grep -q '/home/node' "$CFG" && ok "entries: no container path in the config" || bad "entries: /home/node leaked into the config"

# set-model moves default, allow-list and every agent that followed it
OPENCLAW_SCHEMA=entries OPENCLAW_STATE_DIR="$E" OPENCLAW_HOME_DIR="$E" OPENCLAW_GATEWAY_BIND=loopback \
  OLLAMA_BASE_URL=http://127.0.0.1:11434 "$ROOT_DIR/scripts/set-model.sh" gemma3:4b >/dev/null 2>&1 || true
check "entries: set-model.sh moves the default, the allow-list and the agents" \
  jq -e '.agents.defaults.model.primary == "ollama/gemma3:4b" and (.agents.defaults.modelPolicy.allow | index("ollama/gemma3:4b")) and ((.agents.defaults.modelPolicy.allow | index("ollama/nemotron-3-nano:4b")) | not) and ([.agents.entries[].model.primary] | all(. == "ollama/gemma3:4b"))' "$CFG"

# an id the new schema rejects is refused, by name
LONG="$T/INDEX-long.json"
jq '.agents[0].agentId = "a-machine-agent-id-that-is-much-longer-than-the-sixty-four-character-limit-of-openclaw"' "$INDEX" > "$LONG"
E2="$T/long"; mkdir -p "$E2"
run_schema entries "$E2" "$E2" "$ROOT_DIR/scripts/harden-config.sh" >/dev/null
if INDEX_OVERRIDE="$LONG" run_schema entries "$E2" "$E2" "$ROOT_DIR/scripts/sync-machine-agents.sh" >"$T/long.out" 2>&1; then
  bad "entries: a 90-character agent id was accepted"
else
  grep -q "a-machine-agent-id-that-is-much-longer" "$T/long.out" && ok "entries: a 90-character agent id is refused, named" || bad "entries: refused without naming the id"
fi

# a remapped gateway port is generated and verified as the same port
E3="$T/port"; mkdir -p "$E3"
run_schema entries "$E3" "$E3" env OPENCLAW_GATEWAY_PORT=18795 "$ROOT_DIR/scripts/harden-config.sh" >/dev/null
run_schema entries "$E3" "$E3" env OPENCLAW_GATEWAY_PORT=18795 "$ROOT_DIR/scripts/sync-machine-agents.sh" >/dev/null
OPENCLAW_GATEWAY_PORT=18795 run_schema entries "$E3" "$E3" "$ROOT_DIR/scripts/verify-openclaw-config.sh" >/dev/null 2>&1 \
  && ok "entries: a remapped gateway port (18795) is verified as configured" || bad "entries: a remapped port fails the verifier"
check "entries: the remapped port is in the config and its origins" \
  jq -e '.gateway.port == 18795 and (.gateway.controlUi.allowedOrigins | index("http://127.0.0.1:18795"))' "$E3/openclaw.json"

# a browser sidecar is attached over CDP from the config
E4="$T/browser"; mkdir -p "$E4"
run_schema entries "$E4" "$E4" env OPENCLAW_BROWSER_CDP_URL=http://127.0.0.1:9222 "$ROOT_DIR/scripts/harden-config.sh" >/dev/null
run_schema entries "$E4" "$E4" env OPENCLAW_BROWSER_CDP_URL=http://127.0.0.1:9222 "$ROOT_DIR/scripts/sync-machine-agents.sh" >/dev/null
check "entries: the browser sidecar is an attach-only CDP profile" \
  jq -e '.browser.attachOnly == true and .browser.defaultProfile == "sidecar" and .browser.profiles.sidecar.cdpUrl == "http://127.0.0.1:9222" and (.browser.ssrfPolicy.allowedHostnames == ["host.docker.internal"])' "$E4/openclaw.json"
check "entries: no browser block without a sidecar" jq -e 'has("browser") | not' "$E/openclaw.json"
OC="$(command -v openclaw || true)"
[[ -n "$OC" ]] || for n in "$HOME"/.nvm/versions/node/*/bin/openclaw; do [[ -x "$n" ]] && OC="$n"; done
if [[ -n "$OC" ]]; then
  PATH="$(dirname "$OC"):$PATH" OPENCLAW_STATE_DIR="$E4" OPENCLAW_CONFIG_PATH="$E4/openclaw.json" openclaw config validate >/dev/null 2>&1 \
    && ok "openclaw accepts the browser block" || bad "openclaw rejects the browser block"
fi

# ── list (container image) is unchanged in shape ─────────────────────────────
L="$T/container"; mkdir -p "$L"
run_schema list "$L" /home/node/.openclaw "$ROOT_DIR/scripts/harden-config.sh" >/dev/null
OPENCLAW_GATEWAY_BIND=lan run_schema list "$L" /home/node/.openclaw "$ROOT_DIR/scripts/sync-machine-agents.sh" >/dev/null
check "list: agents.list holds main + 3 agents, container paths" \
  jq -e '(.agents.list | length) == 4 and (.agents | has("entries") | not) and (.agents.list[0].workspace == "/home/node/.openclaw/workspace") and (.gateway.nodes.denyCommands | index("sms.send"))' "$L/openclaw.json"
OPENCLAW_GATEWAY_BIND=lan run_schema list "$L" /home/node/.openclaw "$ROOT_DIR/scripts/verify-openclaw-config.sh" >/dev/null 2>&1 \
  && ok "list: the verifier passes" || bad "list: the verifier rejects the generated config"

# ── the real gateway's own validator ─────────────────────────────────────────
OC="$(command -v openclaw || true)"
[[ -n "$OC" ]] || for n in "$HOME"/.nvm/versions/node/*/bin/openclaw; do [[ -x "$n" ]] && OC="$n"; done
if [[ -n "$OC" ]]; then
  # The config validator reads the config in place; the state dir keeps it away from ~/.openclaw.
  # (The node that runs it is the one openclaw's directory sits beside.)
  if PATH="$(dirname "$OC"):$PATH" OPENCLAW_STATE_DIR="$E" OPENCLAW_CONFIG_PATH="$CFG" openclaw config validate >"$T/validate.out" 2>&1; then
    ok "openclaw $(PATH="$(dirname "$OC"):$PATH" openclaw --version 2>/dev/null | head -1) accepts the entries config"
  else
    bad "openclaw config validate rejects the entries config"; sed 's/^/       /' "$T/validate.out" | head -20
  fi
else
  echo "  SKIPPED  openclaw config validate: no openclaw on PATH (the generated config was NOT checked against the real gateway)"
fi

[[ "$fails" -eq 0 ]] && { echo "all ok"; exit 0; }
echo "$fails failure(s)"; exit 1
