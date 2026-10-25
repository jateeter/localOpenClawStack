#!/usr/bin/env bash
# =============================================================================
# test-set-model.sh — scripts/set-model.sh moves every agent that followed the
# old default, not just agents.defaults.
#
# On 2026-10-24 a switch from llama3.1:8b to nemotron-3-nano:4b rewrote only
# agents.defaults.model.primary. The main agent kept llama3.1:8b, because
# sync-machine-agents.sh preserves main's existing model, and the universe's
# startup said nothing.
#
# Sandboxed: runs set-model.sh, harden-config.sh and adopt-config-baseline.sh
# from a scratch copy of scripts/ against a synthetic openclaw.json. The live
# openclaw/ and .env are never read or written, and no container is touched.
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v jq >/dev/null || { echo "[FAIL] jq is required" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/scripts" "$TMP/openclaw"
cp -R "$ROOT_DIR"/scripts/. "$TMP/scripts/"
: > "$TMP/.env"
cat > "$TMP/openclaw/openclaw.json" <<'JSON'
{"agents": {"defaults": {"model": {"primary": "ollama/llama3.1:8b"}, "models": {},
                         "modelPolicy": {"allow": ["ollama/llama3.1:8b", "openai/gpt-5.5"]}},
            "entries": {"main": {"model": {"primary": "ollama/llama3.1:8b"}},
                        "machine-a": {"model": {"primary": "ollama/llama3.1:8b"}},
                        "pinned": {"model": {"primary": "openai/gpt-5.5"}},
                        "inherits": {}}}}
JSON

out="$("$TMP/scripts/set-model.sh" ollama/nemotron-3-nano:4b 2>/dev/null)"
cfg="$TMP/openclaw/openclaw.json"

fails=0
check() { # <name> <expected> <actual>
  if [ "$2" == "$3" ]; then echo "  ok   $1"
  else echo "  FAIL $1"; echo "       expected: $2"; echo "       actual:   $3"; fails=$((fails + 1)); fi
}
model_of() { jq -r --arg id "$1" '.agents.entries[$id].model.primary // "none"' "$cfg"; }

check "default moves"              "ollama/nemotron-3-nano:4b" "$(jq -r '.agents.defaults.model.primary' "$cfg")"
check "main moves with the default" "ollama/nemotron-3-nano:4b" "$(model_of main)"
check "agent on old default moves" "ollama/nemotron-3-nano:4b" "$(model_of machine-a)"
check "pinned agent keeps its model" "openai/gpt-5.5"           "$(model_of pinned)"
check "agent without a model still inherits" "none"             "$(model_of inherits)"
check "kept agent is reported"     "1" "$(grep -c '^kept: agent pinned stays on openai/gpt-5.5$' <<<"$out")"
check "provider lists the new model" "1" "$(jq '[.models.providers.ollama.models[] | select(.id == "nemotron-3-nano:4b")] | length' "$cfg")"
check "allowlist gains the new model" "1" "$(jq '[.agents.defaults.modelPolicy.allow[] | select(. == "ollama/nemotron-3-nano:4b")] | length' "$cfg")"
check "baseline adopted"           "yes" "$(if [ -f "$TMP/openclaw/openclaw.json.last-good" ]; then echo yes; else echo no; fi)"
check "no llama assignment left"   "0" "$(jq '[.agents.defaults, .agents.entries[]] | map(select(.model.primary == "ollama/llama3.1:8b")) | length' "$cfg")"

if [ "$fails" -ne 0 ]; then echo "$fails check(s) failed"; exit 1; fi
echo "all checks passed"
