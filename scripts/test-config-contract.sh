#!/usr/bin/env bash
# =============================================================================
# test-config-contract.sh — regression guard against generator/validator drift.
#
# harden-config.sh (generator) and verify-openclaw-config.sh (validator) are two
# independent encodings of the OpenClaw gateway security contract. When only one
# side is edited they drift, and the failure only surfaces during a live deploy
# (see issue #8: the validator required .gateway.port / .gateway.nodes.denyCommands
# that the generator never emitted).
#
# This test regenerates the config with the real generator and asserts it
# satisfies the gateway security contract, so any future one-sided change fails
# in CI instead of in production. Run it in `make test` and on PRs touching
# scripts/harden-config.sh or scripts/verify-openclaw-config.sh.
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v jq >/dev/null || { echo "[FAIL] jq is required" >&2; exit 1; }

CONFIG="openclaw/openclaw.json"

# Protect the operator's live config: back it up and restore on exit. Only
# openclaw.json is touched by harden-config.sh (it does not populate agents).
BACKUP=""
if [[ -f "$CONFIG" ]]; then BACKUP="$(mktemp)"; cp "$CONFIG" "$BACKUP"; fi
restore() {
  if [[ -n "$BACKUP" ]]; then mv "$BACKUP" "$CONFIG"; else rm -f "$CONFIG"; fi
}
trap restore EXIT

# Regenerate the gateway config exactly as the deployment does.
rm -f "$CONFIG"
scripts/harden-config.sh >/dev/null

# Assert the generated config satisfies every GENERATOR-OWNED contract that
# scripts/verify-openclaw-config.sh enforces — gateway security, the Ollama
# provider, and the small-model web/browser tool deny. (Agent-population
# contracts are owned by sync-machine-agents.sh and validated during deploy.)
# Keep these predicates in lockstep with verify-openclaw-config.sh; a future
# improvement is to source both from one shared contract file.
if jq -e '
  # gateway security contract
  .gateway.mode == "local" and
  .gateway.auth.mode == "token" and
  .gateway.port == 18789 and
  (.gateway.bind == "lan" or .gateway.bind == "loopback") and
  (.gateway.nodes.denyCommands | type == "array") and
  (.gateway.nodes.denyCommands | index("camera.snap")) and
  (.gateway.nodes.denyCommands | index("screen.record")) and
  (.gateway.nodes.denyCommands | index("sms.send")) and
  # Ollama provider contract
  (.models.providers.ollama.api == "ollama") and
  (.models.providers.ollama.baseUrl | type == "string") and
  (.models.providers.ollama.baseUrl | startswith("http")) and
  (.models.providers.ollama.models | type == "array") and
  (.models.providers.ollama.models | length >= 1) and
  # small-model web/browser tool deny for the default model
  ((.agents.defaults.model.primary) as $m
    | (.tools.byProvider[$m].deny // [])
    | (index("group:web") and index("browser")))
' "$CONFIG" >/dev/null; then
  echo "[PASS] harden-config.sh output satisfies the gateway, Ollama-provider, and tool-deny contracts"
else
  echo "[FAIL] harden-config.sh output violates a generator-owned security contract:" >&2
  jq -c '{gateway, models, toolsDeny: .tools.byProvider}' "$CONFIG" >&2 || true
  exit 1
fi
