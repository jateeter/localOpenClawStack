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

tag="${1:-}"
tag="${tag#ollama/}"
[[ -n "$tag" ]] || { echo "usage: scripts/set-model.sh <ollama-tag>   e.g. nemotron-3-nano:4b" >&2; exit 2; }
[[ -f openclaw/openclaw.json ]] || { echo "openclaw/openclaw.json not found — run scripts/start.sh first" >&2; exit 1; }

if command -v ollama >/dev/null 2>&1 && ! ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$tag"; then
  echo "warning: '$tag' is not installed in Ollama — run: ollama pull $tag" >&2
fi

# Agents that carry the old default as their own model (main always does, and
# sync-machine-agents.sh keeps main's existing model) move with it; an agent
# pinned to some other model keeps it and is named below.
old="$(jq -r '.agents.defaults.model.primary // ""' openclaw/openclaw.json)"
tmp="$(mktemp)"
jq --arg model "ollama/${tag}" --arg old "$old" '
  .agents.defaults.model.primary = $model |
  .agents.list = ((.agents.list // []) | map(
    if (.model.primary // null) == $old then .model.primary = $model else . end))
' openclaw/openclaw.json > "$tmp"
mv "$tmp" openclaw/openclaw.json
jq -r --arg model "ollama/${tag}" '.agents.list[]?
  | select(.model.primary != null and .model.primary != $model)
  | "kept: agent \(.id) stays on \(.model.primary)"' openclaw/openclaw.json

"$ROOT_DIR/scripts/harden-config.sh"
"$ROOT_DIR/scripts/adopt-config-baseline.sh"
echo "OpenClaw default model is now ollama/${tag}. Restart the gateway to apply: scripts/stop.sh && scripts/start.sh"
