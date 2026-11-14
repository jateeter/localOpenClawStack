#!/usr/bin/env bash
# Switch OpenClaw's default model on command.
#
#   scripts/set-model.sh nemotron-3-nano:4b     # an Ollama tag (an "ollama/" prefix is accepted)
#
# OPENCLAW_DEFAULT_MODEL only seeds a *fresh* openclaw.json; an existing config
# keeps the model it was generated with. This rewrites agents.defaults.model.primary
# in the live config, and every agent whose own model was the old default (main
# included: on 2026-10-24 it stayed on llama3.1:8b because only the default
# moved, and sync-machine-agents.sh keeps main's existing model), re-runs harden-config.sh so the Ollama provider lists the
# new model, and adopts the result as the gateway's config baseline (anything that
# rewrites openclaw/openclaw.json must — see CLAUDE.md). Restarting the gateway is
# left to the caller, so a running stack is never changed behind its back.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=lib/oc-config.sh
source "$ROOT_DIR/scripts/lib/oc-config.sh"

tag="${1:-}"
tag="${tag#ollama/}"
[[ -n "$tag" ]] || { echo "usage: scripts/set-model.sh <ollama-tag>   e.g. nemotron-3-nano:4b" >&2; exit 2; }
[[ -f "$OC_CONFIG" ]] || { echo "$OC_CONFIG not found — run scripts/start.sh first" >&2; exit 1; }
SCHEMA="$(oc_schema "$OC_CONFIG")"

if command -v ollama >/dev/null 2>&1 && ! ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$tag"; then
  echo "warning: '$tag' is not installed in Ollama — run: ollama pull $tag" >&2
fi

# Agents that carry the old default as their own model (main always does, and
# sync-machine-agents.sh keeps main's existing model) move with it; an agent
# pinned to some other model keeps it and is named below.
old="$(jq -r '.agents.defaults.model.primary // ""' "$OC_CONFIG")"
tmp="$(mktemp)"
jq --arg model "ollama/${tag}" --arg old "$old" --arg schema "$SCHEMA" "$OC_JQ_DEFS"'
  .agents.defaults.model.primary = $model |
  # The model allow-list moves with the default (modelPolicy.allow on the new schema).
  (if $schema == "entries"
   then .agents.defaults.modelPolicy.allow = (((.agents.defaults.modelPolicy.allow // []) - [$old] + [$model]) | unique)
   else . end) |
  .agents.defaults.models = (((.agents.defaults.models // {}) | del(.[$old])) + {($model): {}}) |
  oc_with_agents(oc_agents | map(
    if (.model.primary // null) == $old then .model.primary = $model else . end); $schema)
' "$OC_CONFIG" > "$tmp"
mv "$tmp" "$OC_CONFIG"
jq -r --arg model "ollama/${tag}" "$OC_JQ_DEFS"'oc_agents[]
  | select(.model.primary != null and .model.primary != $model)
  | "kept: agent \(.id) stays on \(.model.primary)"' "$OC_CONFIG"

"$ROOT_DIR/scripts/harden-config.sh"
"$ROOT_DIR/scripts/adopt-config-baseline.sh"
echo "OpenClaw default model is now ollama/${tag}. Restart the gateway to apply: scripts/stop.sh && scripts/start.sh"
