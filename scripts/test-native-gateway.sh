#!/usr/bin/env bash
# scripts/native-gateway.sh: preflight checks the host install against npm's
# current release and the floor (nothing is pinned, RealityEngine_CI#663) and
# refuses a Node the release can't run on; the LaunchAgent it writes keeps the
# token out and runs on loopback.
#
# Sandboxed: fake `openclaw`, `node` and `npm` binaries in a temp dir, a scratch plist
# path and home. Nothing is loaded into launchd, no gateway starts, and the
# operator's ~/.openclaw, ~/.realityengine and LaunchAgents are not touched.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_NODE="$(command -v node)"
# What the fake npm reports as current. FAKE_CURRENT empty: npm unreadable.
CURRENT=2026.11.3
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

fails=0
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else echo "  FAIL $1 (expected '$2', got '$3')"; fails=$((fails + 1)); fi; }

# fake_install <dir> <openclaw version> <node version> <engines> [npm's current]
fake_install() {
  local d="$1"; mkdir -p "$d/bin" "$d/lib/openclaw"
  if [[ -n "${5-$CURRENT}" ]]; then
    printf '#!/bin/sh\n[ "$1 $2 $3" = "view openclaw dist-tags.latest" ] && echo %s && exit 0\nexit 1\n' "${5-$CURRENT}" > "$d/bin/npm"
  else
    printf '#!/bin/sh\nexit 1\n' > "$d/bin/npm"
  fi
  chmod +x "$d/bin/npm"
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

fake_install "$T/behind" 2026.10.8 26.12.1 ">=24.22.1 <25 || >=26.12.1"
check "an openclaw behind current passes check" 0 "$(run "$T/behind" check)"
if grep -q "behind the current $CURRENT; 'up' updates it" "$T/out"; then check "check says up will update it" yes yes; else check "check says up will update it" yes no; fi
fake_install "$T/old" 2026.6.8 26.12.1 ">=24.22.1 <25 || >=26.12.1"
run "$T/old" check >/dev/null
if grep -q "below the floor" "$T/out"; then check "below the floor is named" yes yes; else check "below the floor is named" yes no; fi
fake_install "$T/offline" 2026.10.8 26.12.1 ">=24.22.1 <25 || >=26.12.1" ""
check "npm unreadable: the installed version is kept" 0 "$(run "$T/offline" check)"
if grep -q "keeping the installed 2026.10.8" "$T/out"; then check "and it says so" yes yes; else check "and it says so" yes no; fi

fake_install "$T/node25" "$CURRENT" 25.9.0 ">=24.22.1 <25 || >=26.12.1"
check "a Node outside the release's engines is refused" 1 "$(run "$T/node25" check)"
fake_install "$T/node2612" "$CURRENT" 26.12.0 ">=24.22.1 <25 || >=26.12.1"
check "Node 26.12.0 is refused (needs >=26.12.1)" 1 "$(run "$T/node2612" check)"
fake_install "$T/node24" "$CURRENT" 24.22.1 ">=24.22.1 <25 || >=26.12.1"
check "the release names one Node exactly (OPENCLAW_NODE_EXACT)" 1 \
  "$(if OPENCLAW_NODE_EXACT=26.12.1 env -u OPENCLAW_VERSION OPENCLAW_BIN="$T/node24/bin/openclaw" "$ROOT_DIR/scripts/native-gateway.sh" check >/dev/null 2>&1; then echo 0; else echo 1; fi)"

fake_install "$T/good" "$CURRENT" 26.12.1 ">=24.22.1 <25 || >=26.12.1"
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
