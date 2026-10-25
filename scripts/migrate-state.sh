#!/usr/bin/env bash
# Bring openclaw/ (config, agent databases, sessions) up to the pinned
# OPENCLAW_VERSION before the gateway starts.
#
# A newer gateway refuses state an older one wrote until `openclaw doctor --fix`
# migrates it, offline. Moving 2026.6.10 → 2026.10.7 on 2026-10-24:
#   - 2026.10.7 refused to boot: "agent database … uses schema version 1; run
#     openclaw doctor --fix", so start.sh failed on an unhealthy container.
#   - 2026.10.7's doctor refused state last written before July 2026: "Upgrade
#     through OpenClaw 2026.9.7, run openclaw doctor --fix, then retry".
#   - 2026.9.7's doctor rewrote the config: agents.list → agents.entries,
#     gateway.nodes.denyCommands → gateway.nodes.commands.deny, and a
#     modelPolicy allowlist.
#
# So: when the config was last written by a version other than the pin, or is
# still in the agents.list format, this backs up openclaw/, stops the gateway,
# runs the doctor at 2026.9.7 first if the state predates it, then at the pin.
# A fresh install (no openclaw.json yet) has nothing to migrate.
#
# Container stack by default (openclaw/, the doctor run in the pinned image). With
# OPENCLAW_STATE_DIR set it migrates a native gateway's state directory with the
# host's own `openclaw`, which must already be the pin; the caller stops that
# gateway first (scripts/native-gateway.sh does).
#
#   scripts/migrate-state.sh            # migrate if needed
#   scripts/migrate-state.sh --check    # exit 1 if a migration is needed, change nothing
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CHECK=false
[[ "${1:-}" == "--check" ]] && CHECK=true

if [[ -f .env ]]; then
  # The caller's environment wins over .env: native-gateway.sh passes the native
  # gateway's values (Ollama on loopback, its port), which .env sets for the
  # container. Sourcing .env alone overwrote them (2026-10-25).
  _caller_env="$(export -p)"
  # shellcheck source=/dev/null
  set -a; source .env; set +a
  eval "$_caller_env"
fi
# shellcheck source=lib/oc-config.sh
source "$ROOT_DIR/scripts/lib/oc-config.sh"
TARGET="${OPENCLAW_VERSION:-$(sed -n 's/^ARG OPENCLAW_VERSION=//p' Dockerfile.openclaw-gateway 2>/dev/null || true)}"
[[ -n "$TARGET" ]] || { echo "[migrate-state] no OpenClaw version to migrate to: set OPENCLAW_VERSION" >&2; exit 1; }
# The last release whose doctor accepts state from before July 2026.
STEP_VERSION="2026.9.7"
NATIVE=false
[[ -n "${OPENCLAW_STATE_DIR:-}" ]] && NATIVE=true

note() { echo "[migrate-state] $*"; }
die() { echo "[migrate-state] $*" >&2; exit 1; }
version_lt() { [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" == "$1" ]]; }

command -v jq >/dev/null || die "jq is required"
[[ -f "$OC_CONFIG" ]] || { note "no $OC_CONFIG yet — nothing to migrate"; exit 0; }

touched="$(jq -r '.meta.lastTouchedVersion // "0"' "$OC_CONFIG")"
legacy=false
[[ "$(oc_schema "$OC_CONFIG")" == list ]] && legacy=true

if [[ "$touched" == "$TARGET" && "$legacy" == false ]]; then
  note "state is current ($TARGET)"
  exit 0
fi
# No stamp, entries format: this stack's scripts wrote it for the pin, and no
# gateway has rewritten it yet. There is nothing older to migrate.
if [[ "$touched" == 0 && "$legacy" == false ]]; then
  note "state was generated for $TARGET and not yet stamped by a gateway; nothing to migrate"
  exit 0
fi
if [[ "$CHECK" == true ]]; then
  note "migration needed: state written by $touched$([[ "$legacy" == true ]] && echo ', agents.list format'), pin is $TARGET"
  exit 1
fi
version_lt "$TARGET" "$touched" && \
  die "state was written by $touched, newer than the pin $TARGET; refusing to downgrade it"

if [[ "$NATIVE" == true ]]; then
  command -v openclaw >/dev/null || die "openclaw is not on PATH"
  host="$(openclaw --version 2>/dev/null | awk '{print $2}')"
  [[ "$host" == "$TARGET" ]] || \
    die "the host openclaw is ${host:-unknown}, not the pin $TARGET: npm install -g openclaw@$TARGET"
  if version_lt "$touched" "$STEP_VERSION" && version_lt "$STEP_VERSION" "$TARGET"; then
    die "state was written by $touched, before $STEP_VERSION, whose doctor must run first; the host has $host only. Move $OC_DIR aside and let the next start generate a fresh one"
  fi
fi

backup_dir="${OPENCLAW_STATE_BACKUP_DIR:-$ROOT_DIR/.state-backups}"
mkdir -p "$backup_dir"; chmod 700 "$backup_dir"
backup="$backup_dir/$(basename "$OC_DIR")-$(date -u +%Y%m%dT%H%M%SZ)-from-$touched.tgz"
tar -czf "$backup" -C "$(dirname "$OC_DIR")" "$(basename "$OC_DIR")"
chmod 600 "$backup"
note "backed up $OC_DIR to $backup"
restore_hint="restore: rm -rf $OC_DIR && tar -xzf $backup -C $(dirname "$OC_DIR")"

[[ "$NATIVE" == false ]] && { docker compose stop openclaw-gateway >/dev/null 2>&1 || true; }

doctor() { # <version>
  local v="$1" image="local/openclaw-gateway:$1"
  note "openclaw doctor --fix at $v"
  if [[ "$NATIVE" == true ]]; then
    OPENCLAW_STATE_DIR="$OC_DIR" OPENCLAW_CONFIG_PATH="$OC_CONFIG" \
      openclaw doctor --fix --non-interactive </dev/null >/dev/null 2>&1 || \
      die "openclaw doctor --fix failed at $v; $restore_hint"
    return
  fi
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    note "building $image for the migration"
    docker build -q -f Dockerfile.openclaw-gateway \
      --build-arg NODE_IMAGE="${NODE_IMAGE:-$(sed -n 's/^ARG NODE_IMAGE=//p' Dockerfile.openclaw-gateway)}" \
      --build-arg OPENCLAW_VERSION="$v" -t "$image" . >/dev/null
  fi
  # </dev/null: `compose run` reads stdin, and would swallow a caller's script.
  OPENCLAW_VERSION="$v" docker compose run --rm --no-deps -T --entrypoint openclaw \
    openclaw-gateway doctor --fix --non-interactive </dev/null >/dev/null 2>&1 || \
    die "openclaw doctor --fix failed at $v; $restore_hint"
}

if [[ "$NATIVE" == false ]] && version_lt "$touched" "$STEP_VERSION" && version_lt "$STEP_VERSION" "$TARGET"; then
  doctor "$STEP_VERSION"
fi
# Twice at the pin: the first pass upgrades the agent databases, and on
# 2026-10-24 asked for a second to migrate session identities.
doctor "$TARGET"
doctor "$TARGET"

[[ "$(oc_schema "$OC_CONFIG")" == entries ]] || \
  die "doctor finished but $OC_CONFIG is still not in the agents.entries format; $restore_hint"
note "migrated $OC_DIR from $touched to $(jq -r '.meta.lastTouchedVersion' "$OC_CONFIG")"
