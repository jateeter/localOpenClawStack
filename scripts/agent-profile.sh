#!/usr/bin/env bash
# Resolve an agent profile name to a machine-behavior agent index, and print the
# index path on stdout.
#
#   ./scripts/agent-profile.sh full         -> machine-behaviors/agents/INDEX.json
#   ./scripts/agent-profile.sh regression   -> .generated/INDEX.regression.json
#
# A named profile is a list of agentIds in machine-behaviors/agents/profiles/.
# This script filters the canonical INDEX.json down to that list and writes a
# well-formed index, so every consumer downstream — sync-machine-agents.sh, the
# start.sh count gate, verify-openclaw-config.sh — keeps reading one shape and
# needs no profile awareness of its own.
#
# The filtered index is generated, not committed; profiles/ holds the source of
# truth. See generate-regression-profile.py for where the regression profile
# itself comes from.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS_DIR="$ROOT_DIR/machine-behaviors/agents"
INDEX_PATH="$AGENTS_DIR/INDEX.json"
GENERATED_DIR="$AGENTS_DIR/.generated"

PROFILE="${1:-${OPENCLAW_AGENT_PROFILE:-full}}"

die() { echo "[agent-profile] $*" >&2; exit 1; }

command -v jq >/dev/null || die "jq is required"
[[ -f "$INDEX_PATH" ]] || die "missing $INDEX_PATH"

if [[ "$PROFILE" == "full" ]]; then
  printf '%s\n' "$INDEX_PATH"
  exit 0
fi

PROFILE_PATH="$AGENTS_DIR/profiles/$PROFILE.txt"
[[ -f "$PROFILE_PATH" ]] || die "unknown agent profile '$PROFILE' (no $PROFILE_PATH)"

# Strip trailing comments and blank lines; a profile line is `agentId  # machine`.
IDS_TMP="$(mktemp)"
trap 'rm -f "$IDS_TMP"' EXIT
sed -E 's/#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//' "$PROFILE_PATH" | grep -v '^$' > "$IDS_TMP"
[[ -s "$IDS_TMP" ]] || die "profile '$PROFILE' selected no agents"

OUT_PATH="$GENERATED_DIR/INDEX.$PROFILE.json"
mkdir -p "$GENERATED_DIR"

# An agentId in the profile that no longer exists in INDEX.json means the corpus
# moved and the profile did not. Refuse rather than silently starting a stack
# with fewer agents than the profile asked for.
UNKNOWN="$(jq -R -s -r --slurpfile idx "$INDEX_PATH" '
  ($idx[0].agents | map(.agentId)) as $known |
  split("\n") | map(select(length > 0)) | map(select(. as $id | ($known | index($id)) | not)) | join(" ")
' "$IDS_TMP")"
[[ -z "$UNKNOWN" ]] || die "profile '$PROFILE' names agents absent from INDEX.json: $UNKNOWN"

jq -R -s --slurpfile idx "$INDEX_PATH" --arg profile "$PROFILE" '
  (split("\n") | map(select(length > 0))) as $wanted |
  ($idx[0].agents | map(select(.agentId as $id | $wanted | index($id)))) as $selected |
  {
    total: ($selected | length),
    profile: $profile,
    byDomain: ($selected | group_by(.domain) | map({key: .[0].domain, value: length}) | from_entries),
    agents: $selected
  }
' "$IDS_TMP" > "$OUT_PATH.tmp"

jq . "$OUT_PATH.tmp" >/dev/null || die "generated index is not valid JSON"
mv "$OUT_PATH.tmp" "$OUT_PATH"

printf '%s\n' "$OUT_PATH"
