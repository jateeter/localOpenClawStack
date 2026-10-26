#!/usr/bin/env bash
# resolve_bins (scripts/native-gateway.sh) must succeed, under set -e, whatever
# docker candidates exist (RealityEngine_CI#592).
#
# Its last statement was a loop over docker locations ending in `[[ -x ... ]] &&`.
# On a Mac whose last candidate (~/.docker/bin/docker) does not exist the loop
# returned 1, and the script exited silently, before any check or message. The
# other session's Mac had that file; this one did not.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fails=0
ok()  { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails + 1)); }

mkdir -p "$T/bin" "$T/home"
printf '#!/bin/sh\necho "OpenClaw 1.0.0"\n' > "$T/bin/openclaw"
printf '#!/bin/sh\necho v1.0.0\n' > "$T/bin/node"
chmod +x "$T/bin/openclaw" "$T/bin/node"

# The function, as the script defines it, run under errexit with a stub environment.
fn="$(sed -n '/^resolve_bins() {/,/^}/p' "$ROOT_DIR/scripts/native-gateway.sh")"
[[ -n "$fn" ]] || { echo "  FAIL resolve_bins not found"; exit 1; }
run() { # HOME-with-or-without-docker -> prints BIN_PATH
  HOME="$1" PIN=1.0.0 OPENCLAW_BIN="$T/bin/openclaw" bash -c '
    set -euo pipefail
    die() { echo "die: $*" >&2; exit 9; }
    '"$fn"'
    resolve_bins
    echo "BIN_PATH=$BIN_PATH"'
}

echo "native-gateway resolve_bins"
if out="$(run "$T/home" 2>&1)"; then ok "succeeds with no ~/.docker/bin/docker"; else bad "exits $? with no ~/.docker/bin/docker: $out"; fi
case "$out" in *"BIN_PATH=$T/bin:$T/bin:/usr/bin"*) ok "the node and openclaw directories lead the path" ;; *) bad "unexpected BIN_PATH: $out" ;; esac

mkdir -p "$T/home/.docker/bin"; printf '#!/bin/sh\n' > "$T/home/.docker/bin/docker"; chmod +x "$T/home/.docker/bin/docker"
out2="$(run "$T/home" 2>&1)" && ok "succeeds with ~/.docker/bin/docker" || bad "exits with ~/.docker/bin/docker: $out2"
case "$out2" in *":$T/home/.docker/bin"*) ok "an existing docker directory is added" ;; *) bad "docker directory missing from: $out2" ;; esac

[[ "$fails" -eq 0 ]] && { echo "all ok"; exit 0; }
echo "$fails failure(s)"; exit 1
