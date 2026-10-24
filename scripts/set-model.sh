#!/usr/bin/env bash
# Switch OpenClaw's default model on command.
#
#   scripts/set-model.sh nemotron-3-nano:4b     # an Ollama tag (an "ollama/" prefix is accepted)
#
# OPENCLAW_DEFAULT_MODEL only seeds a *fresh* openclaw.json; an existing config
# keeps the model it was generated with. This rewrites agents.defaults.model.primary
# in the live config, re-runs harden-config.sh so the Ollama provider lists the
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

tmp="$(mktemp)"
jq --arg model "ollama/${tag}" '.agents.defaults.model.primary = $model' openclaw/openclaw.json > "$tmp"
mv "$tmp" openclaw/openclaw.json

"$ROOT_DIR/scripts/harden-config.sh"
"$ROOT_DIR/scripts/adopt-config-baseline.sh"
echo "OpenClaw default model is now ollama/${tag}. Restart the gateway to apply: scripts/stop.sh && scripts/start.sh"
