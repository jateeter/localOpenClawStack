#!/usr/bin/env bash
# scripts/migrate-state.sh decides correctly whether openclaw/ needs migrating.
#
# Sandboxed: a scratch copy of the stack (scripts/, Dockerfile, no .env) with a
# synthetic openclaw.json. Only the paths that return before Docker are run:
# --check, "nothing to migrate", "current", and the downgrade refusal. The
# doctor itself is exercised by start.sh against real state.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/scripts/lib"
cp "$ROOT_DIR/scripts/migrate-state.sh" "$TMP/scripts/"
cp "$ROOT_DIR/scripts/lib/oc-config.sh" "$ROOT_DIR/scripts/lib/openclaw-version.sh" "$TMP/scripts/lib/"
cp "$ROOT_DIR/Dockerfile.openclaw-gateway" "$TMP/"
# The version the caller names as the target (start.sh and native-gateway.sh
# export OPENCLAW_VERSION); nothing is pinned (RealityEngine_CI#663).
PIN=2026.11.3

fails=0
check() { # <name> <expected> <actual>
  if [ "$2" == "$3" ]; then echo "  ok   $1"
  else echo "  FAIL $1 (expected '$2', got '$3')"; fails=$((fails + 1)); fi
}
run() { if OPENCLAW_VERSION="$PIN" "$TMP/scripts/migrate-state.sh" "$@" >/dev/null 2>&1; then echo 0; else echo 1; fi; }
config() { mkdir -p "$TMP/openclaw"; printf '%s\n' "$1" > "$TMP/openclaw/openclaw.json"; }

check "no config: nothing to migrate" 0 "$(run)"

config "{\"meta\":{\"lastTouchedVersion\":\"$PIN\"},\"agents\":{\"entries\":{}}}"
check "written by the pin: current" 0 "$(run --check)"

config "{\"meta\":{\"lastTouchedVersion\":\"$PIN\"},\"agents\":{\"list\":[]}}"
check "agents.list at the pin: needs migrating" 1 "$(run --check)"

config '{"agents":{"entries":{}}}'
check "generated, not yet stamped: current" 0 "$(run --check)"

config '{"agents":{"list":[]}}'
check "unstamped agents.list: needs migrating" 1 "$(run --check)"

# Unstamped, but a gateway has run on it: its agent databases need the doctor.
config '{"agents":{"entries":{}}}'
mkdir -p "$TMP/openclaw/agents/main/agent"; : > "$TMP/openclaw/agents/main/agent/openclaw-agent.sqlite"
check "unstamped with agent databases: needs migrating" 1 "$(run --check)"
rm -rf "$TMP/openclaw/agents"

config '{"meta":{"lastTouchedVersion":"2026.6.10"},"agents":{"list":[]}}'
check "older version: needs migrating" 1 "$(run --check)"

config '{"meta":{"lastTouchedVersion":"2099.1.1"},"agents":{"entries":{}}}'
check "newer than the pin: refused" 1 "$(run)"
# A container target with neither OPENCLAW_VERSION nor a readable npm is refused.
nonpm="$TMP/nonpm"; mkdir -p "$nonpm"; printf '#!/bin/sh\nexit 1\n' > "$nonpm/npm"; chmod +x "$nonpm/npm"
config '{"meta":{"lastTouchedVersion":"2026.6.10"},"agents":{"list":[]}}'
check "no target (npm unreadable, none named): refused" 1 \
  "$(if PATH="$nonpm:$PATH" env -u OPENCLAW_VERSION "$TMP/scripts/migrate-state.sh" --check >/dev/null 2>&1; then echo 0; else echo 1; fi)"
# A native state dir migrates to the host's installed openclaw: state newer
# than it is a downgrade, refused.
N="$TMP/native"; mkdir -p "$N"
printf '%s\n' '{"meta":{"lastTouchedVersion":"2026.6.10"},"agents":{"list":[]}}' > "$N/openclaw.json"
fake="$TMP/bin"; mkdir -p "$fake"; printf '#!/bin/sh\necho "OpenClaw 2026.1.1 (fake)"\n' > "$fake/openclaw"; chmod +x "$fake/openclaw"
check "native: state newer than the installed openclaw is refused" 1 \
  "$(if PATH="$fake:$PATH" OPENCLAW_STATE_DIR="$N" OPENCLAW_STATE_BACKUP_DIR="$TMP/nb" env -u OPENCLAW_VERSION "$TMP/scripts/migrate-state.sh" >/dev/null 2>&1; then echo 0; else echo 1; fi)"
check "native: --check sees the old state" 1 "$(if OPENCLAW_STATE_DIR="$N" env -u OPENCLAW_VERSION "$TMP/scripts/migrate-state.sh" --check >/dev/null 2>&1; then echo 0; else echo 1; fi)"
check "refusal made no backup" 0 "$(find "$TMP" -name '*.tgz' | wc -l | tr -d ' ')"

if [ "$fails" -ne 0 ]; then echo "$fails check(s) failed"; exit 1; fi
echo "all checks passed"
