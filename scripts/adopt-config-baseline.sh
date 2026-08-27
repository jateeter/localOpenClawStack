#!/usr/bin/env bash
# Adopt the current openclaw.json as OpenClaw's trusted config baseline.
#
# OpenClaw's gateway protects itself against a config file being clobbered by a
# stray editor or a truncated write. On startup it reads the config with
# recovery enabled and compares it against a baseline: the `lastKnownGood`
# fingerprint recorded in `openclaw/logs/config-health.json`, falling back to a
# fingerprint of `openclaw.json.bak`. If the live file is under half the
# baseline's size it records `size-drop-vs-last-good:<old>-><new>`, moves the
# live file aside as `openclaw.json.clobbered.<timestamp>`, and restores the
# backup.
#
# Narrowing the agent profile is exactly that shape: 1320 agents (~1.2 MB) down
# to 12 (~12 KB). The gateway cannot tell a deliberate profile change from a
# truncation, so every restart undid the sync and put the previous profile's
# agents.list back — a profile the corpus had since moved past. See issue #30.
#
# The guard is worth keeping, so instead of defeating it we move its baseline
# with us: whenever we deliberately rewrite the config, the backups it compares
# against become copies of that same config, and the stale fingerprints recorded
# in the health state are dropped so the fresh backup is what gets read. A real
# clobber — a truncation nobody asked for — still trips the guard, because
# nothing will have adopted it as the baseline.
#
# Idempotent, and safe to run with the gateway up or down: the gateway re-reads
# both files on its next start and re-promotes its own last-known-good from
# there.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$ROOT_DIR/openclaw/openclaw.json}"
BACKUP_PATH="$CONFIG_PATH.bak"
LAST_GOOD_PATH="$CONFIG_PATH.last-good"
HEALTH_PATH="${OPENCLAW_CONFIG_HEALTH_PATH:-$ROOT_DIR/openclaw/logs/config-health.json}"

note() { echo "[config-baseline] $*"; }
die()  { echo "[config-baseline] $*" >&2; exit 1; }

command -v jq >/dev/null || die "jq is required"
[[ -f "$CONFIG_PATH" ]] || die "missing $CONFIG_PATH"

# Refuse to promote something the gateway would itself reject. `gateway.mode` is
# the field the guard treats as proof a config is a real config, so adopting a
# baseline without it would arm the guard against every later start.
jq -e '.gateway.mode == "local" and ((.agents.list | type) == "array")' "$CONFIG_PATH" >/dev/null 2>&1 || \
  die "refusing to adopt $CONFIG_PATH as baseline: gateway.mode is not \"local\" or agents.list is missing"

copy_baseline() {
  local target="$1" tmp
  if cmp -s "$CONFIG_PATH" "$target" 2>/dev/null; then
    return 1
  fi
  tmp="$(mktemp "$(dirname "$target")/.$(basename "$target").XXXXXX")"
  cat "$CONFIG_PATH" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$target"
  return 0
}

UPDATED=()
copy_baseline "$BACKUP_PATH"    && UPDATED+=("$(basename "$BACKUP_PATH")")
copy_baseline "$LAST_GOOD_PATH" && UPDATED+=("$(basename "$LAST_GOOD_PATH")")

# The recorded fingerprint takes precedence over the backup file, so refreshing
# the backups alone would leave the old size on record and change nothing. The
# health state is keyed by the *container's* config path, which this script has
# no reliable way to reconstruct, so match on the file name instead.
if [[ -f "$HEALTH_PATH" ]]; then
  CONFIG_BASENAME="$(basename "$CONFIG_PATH")"
  HEALTH_TMP="$(mktemp "$(dirname "$HEALTH_PATH")/.config-health.XXXXXX")"
  if jq --arg name "$CONFIG_BASENAME" '
      .entries = ((.entries // {}) | with_entries(
        if ((.key | split("/") | last) == $name) then
          .value |= del(.lastKnownGood, .lastPromotedGood, .lastObservedSuspiciousSignature)
        else . end
      ))
    ' "$HEALTH_PATH" > "$HEALTH_TMP" 2>/dev/null && jq . "$HEALTH_TMP" >/dev/null 2>&1; then
    if ! cmp -s "$HEALTH_PATH" "$HEALTH_TMP"; then
      chmod 600 "$HEALTH_TMP"
      mv "$HEALTH_TMP" "$HEALTH_PATH"
      UPDATED+=("$(basename "$HEALTH_PATH")")
    else
      rm -f "$HEALTH_TMP"
    fi
  else
    rm -f "$HEALTH_TMP"
    note "warning: could not rewrite $HEALTH_PATH; a stale last-known-good fingerprint may still trip the clobber guard"
  fi
fi

chmod 600 "$CONFIG_PATH" 2>/dev/null || true

AGENT_COUNT="$(jq -r '.agents.list | length' "$CONFIG_PATH")"
if [[ ${#UPDATED[@]} -eq 0 ]]; then
  note "baseline already matches openclaw.json ($AGENT_COUNT agents)"
else
  note "adopted openclaw.json ($AGENT_COUNT agents) as the config baseline: ${UPDATED[*]}"
fi
