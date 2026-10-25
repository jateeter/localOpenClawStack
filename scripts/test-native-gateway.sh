#!/usr/bin/env bash
# scripts/native-gateway.sh: preflight refuses the wrong host install, and the
# LaunchAgent it writes keeps the token out and runs on loopback.
#
# Sandboxed: fake `openclaw` and `node` binaries in a temp dir, a scratch plist
# path and home. Nothing is loaded into launchd, no gateway starts, and the
# operator's ~/.openclaw, ~/.realityengine and LaunchAgents are not touched.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_NODE="$(command -v node)"
PIN="$(sed -n 's/^ARG OPENCLAW_VERSION=//p' "$ROOT_DIR/Dockerfile.openclaw-gateway")"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

fails=0
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (expected '$2', got '$3')"; fails=$((fails + 1)); fi; }

# fake_install <dir> <openclaw version> <node version> <engines>
fake_install() {
  local d="$1"; mkdir -p "$d/bin" "$d/lib/openclaw"
  printf '{"name":"openclaw","version":"%s","engines":{"node":"%s"}}\n' "$2" "$4" > "$d/lib/openclaw/package.json"
  printf '#!/bin/sh\necho "OpenClaw %s (fake)"\n' "$2" > "$d/lib/openclaw/openclaw.mjs"
  chmod +x "$d/lib/openclaw/openclaw.mjs"
  ln -s ../lib/openclaw/openclaw.mjs "$d/bin/openclaw"
  printf '#!/bin/sh\nif [ "$1" = --version ]; then echo v%s; else exec "%s" "$@"; fi\n' "$3" "$REAL_NODE" > "$d/bin/node"
  chmod +x "$d/bin/node"
}
run() { # <install dir> <subcommand> -> exit code
  if env -u OPENCLAW_VERSION OPENCLAW_BIN="$1/bin/openclaw" OPENCLAW_NATIVE_HOME="$T/home" \
       OPENCLAW_NATIVE_PLIST="$T/agent.plist" "$ROOT_DIR/scripts/native-gateway.sh" "$2" >"$T/out" 2>&1
  then echo 0; else echo 1; fi
}

fake_install "$T/old" 2026.6.8 26.12.1 ">=24.22.1 <25 || >=26.12.1"
check "an openclaw other than the pin is refused" 1 "$(run "$T/old" check)"
grep -q "npm install -g openclaw@$PIN" "$T/out" && check "the refusal names the install command" yes yes || check "the refusal names the install command" yes no

fake_install "$T/node25" "$PIN" 25.9.0 ">=24.22.1 <25 || >=26.12.1"
check "a Node outside the release's engines is refused" 1 "$(run "$T/node25" check)"
fake_install "$T/node2612" "$PIN" 26.12.0 ">=24.22.1 <25 || >=26.12.1"
check "Node 26.12.0 is refused (needs >=26.12.1)" 1 "$(run "$T/node2612" check)"
fake_install "$T/node24" "$PIN" 24.22.1 ">=24.22.1 <25 || >=26.12.1"
check "the release names one Node exactly (OPENCLAW_NODE_EXACT)" 1 \
  "$(if OPENCLAW_NODE_EXACT=26.12.1 env -u OPENCLAW_VERSION OPENCLAW_BIN="$T/node24/bin/openclaw" "$ROOT_DIR/scripts/native-gateway.sh" check >/dev/null 2>&1; then echo 0; else echo 1; fi)"

fake_install "$T/good" "$PIN" 26.12.1 ">=24.22.1 <25 || >=26.12.1"
check "render writes the LaunchAgent" 0 "$(run "$T/good" render)"
P="$T/agent.plist"
check "the plist is valid" 0 "$(plutil -lint "$P" >/dev/null 2>&1 && echo 0 || echo 1)"
check "it runs native-gateway-run.sh" "$ROOT_DIR/scripts/native-gateway-run.sh" "$(plutil -extract ProgramArguments.1 raw "$P")"
check "its state dir is the native home" "$T/home" "$(plutil -extract EnvironmentVariables.OPENCLAW_STATE_DIR raw "$P")"
check "its node comes first on PATH" "$T/good/bin" "$(plutil -extract EnvironmentVariables.PATH raw "$P" | cut -d: -f1)"
check "it holds no token" 0 "$(grep -ciE 'token' "$P" || true)"
check "it is private" 600 "$(stat -f %Lp "$P")"
check "the gateway binds loopback" 1 "$(grep -c -- '--bind loopback' "$ROOT_DIR/scripts/native-gateway-run.sh")"

if [[ "$fails" -ne 0 ]]; then echo "$fails check(s) failed"; exit 1; fi
echo "all checks passed"
