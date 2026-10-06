#!/usr/bin/env bash
# Resolve an agent profile name to a machine-behavior agent index, and print the
# index path on stdout.
#
#   ./scripts/agent-profile.sh full         -> machine-behaviors/agents/INDEX.json
#   ./scripts/agent-profile.sh regression   -> .generated/INDEX.regression.json
#   ./scripts/agent-profile.sh manifest:/path/to/corpus.txt
#                                           -> .generated/INDEX.manifest-<sha>.json
#
# manifest:<path> is the agents of exactly the machines that manifest deploys
# (OPENCLAW_CORPUS_MACHINES_ROOT resolves its entries; default: the sibling
# RealityEngine_Machines/machines).
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

mkdir -p "$GENERATED_DIR"
MANIFEST=""
MANIFEST_SHA=""
if [[ "$PROFILE" == manifest:* ]]; then
  # The agents of exactly the machines a corpus manifest deploys. This is how
  # OpenClaw follows the corpus the engines actually load: startUniverse.sh
  # passes the manifest it materialised, and verify-agents-match-corpus.py passes
  # the one the running engines report. A profile chosen by corpus *name* drifted
  # from it: standard-deployment (12 machines) loaded the regression profile's 15.
  MANIFEST="${PROFILE#manifest:}"
  [[ -f "$MANIFEST" ]] || die "manifest not found: $MANIFEST"
  MANIFEST_SHA="$(shasum -a 256 "$MANIFEST" | cut -c1-12)"
  PROFILE_PATH="$GENERATED_DIR/profile.manifest-$MANIFEST_SHA.txt"
  gen_args=(--manifest "$MANIFEST" --out "$PROFILE_PATH" --allow-empty)
  [[ -n "${OPENCLAW_CORPUS_MACHINES_ROOT:-}" ]] && gen_args+=(--machines-root "$OPENCLAW_CORPUS_MACHINES_ROOT")
  python3 "$ROOT_DIR/scripts/generate-regression-profile.py" "${gen_args[@]}" >/dev/null \
    || die "cannot derive agents from manifest $MANIFEST"
  LABEL="manifest:$(basename "$MANIFEST")"
  OUT_PATH="$GENERATED_DIR/INDEX.manifest-$MANIFEST_SHA.json"
else
  PROFILE_PATH="$AGENTS_DIR/profiles/$PROFILE.txt"
  [[ -f "$PROFILE_PATH" ]] || die "unknown agent profile '$PROFILE' (no $PROFILE_PATH)"
  LABEL="$PROFILE"
  OUT_PATH="$GENERATED_DIR/INDEX.$PROFILE.json"
fi

# Strip trailing comments and blank lines; a profile line is `agentId  # machine`.
IDS_TMP="$(mktemp)"
trap 'rm -f "$IDS_TMP"' EXIT
sed -E 's/#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//' "$PROFILE_PATH" | { grep -v '^$' || true; } > "$IDS_TMP"
# A named profile that selects nothing is a broken profile. A manifest may be
# agent-free by rule (arbitration fixtures), and then main alone is correct.
[[ -s "$IDS_TMP" || -n "$MANIFEST" ]] || die "profile '$PROFILE' selected no agents"

# An agentId in the profile that no longer exists in INDEX.json means the corpus
# moved and the profile did not. Refuse rather than silently starting a stack
# with fewer agents than the profile asked for.
UNKNOWN="$(jq -R -s -r --slurpfile idx "$INDEX_PATH" '
  ($idx[0].agents | map(.agentId)) as $known |
  split("\n") | map(select(length > 0)) | map(select(. as $id | ($known | index($id)) | not)) | join(" ")
' "$IDS_TMP")"
[[ -z "$UNKNOWN" ]] || die "profile '$PROFILE' names agents absent from INDEX.json: $UNKNOWN"

jq -R -s --slurpfile idx "$INDEX_PATH" --arg profile "$LABEL" --arg manifest "$MANIFEST" --arg sha "$MANIFEST_SHA" '
  (split("\n") | map(select(length > 0))) as $wanted |
  ($idx[0].agents | map(select(.agentId as $id | $wanted | index($id)))) as $selected |
  {
    total: ($selected | length),
    profile: $profile,
    manifest: (if $manifest == "" then null else $manifest end),
    manifestSha256: (if $sha == "" then null else $sha end),
    byDomain: ($selected | group_by(.domain) | map({key: .[0].domain, value: length}) | from_entries),
    agents: $selected
  }
' "$IDS_TMP" > "$OUT_PATH.tmp"

jq . "$OUT_PATH.tmp" >/dev/null || die "generated index is not valid JSON"
mv "$OUT_PATH.tmp" "$OUT_PATH"

printf '%s\n' "$OUT_PATH"
