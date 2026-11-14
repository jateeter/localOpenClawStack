#!/usr/bin/env bash
# test-secrets-home.sh — the stack's secrets live in the suite's secrets home
# (scripts/lib/secrets-home.sh; owner, 2026-11-14), and init-secrets.sh moves
# them there from .env without changing a value or deleting .env's copy.
#
# Self-contained: a scratch copy of the repo's scripts, a scratch .env and a
# scratch RE_SECRETS_DIR. No gateway, no Docker; the operator's .env, secrets
# home and ~/.realityengine are not touched.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok() { echo "PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1"; FAIL=$((FAIL+1)); }
mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

repo() { # a scratch checkout with the scripts and a .env
  local r="$T/$1"; mkdir -p "$r/scripts/lib"
  cp "$ROOT_DIR/scripts/init-secrets.sh" "$r/scripts/"; cp "$ROOT_DIR/scripts/lib/secrets-home.sh" "$r/scripts/lib/"
  cp "$ROOT_DIR/.env.example" "$r/.env.example"
  printf '%s\n' "$r"
}
init() { LOCAL_OWNER="$(id -un)" LOCAL_GROUP="$(id -gn)" RE_SECRETS_DIR="$2" bash "$1/scripts/init-secrets.sh" >/dev/null; }

# A checkout whose .env already holds real secrets: copied, same values, .env kept.
A="$(repo a)"; H="$T/home"
printf 'OPENCLAW_GATEWAY_TOKEN=tok-a\nWEBUI_SECRET_KEY=key-a\nWEBUI_ADMIN_PASSWORD=pw-a\nWEBUI_ADMIN_EMAIL=me@localhost\nWEBUI_ADMIN_NAME=me\n' > "$A/.env"
init "$A" "$H"
F="$H/localOpenClawStack/secrets.env"
[[ "$(grep -c . "$F")" == 3 ]] && grep -qx 'OPENCLAW_GATEWAY_TOKEN=tok-a' "$F" && grep -qx 'WEBUI_ADMIN_PASSWORD=pw-a' "$F" \
  && ok "existing .env secrets are copied to the home unchanged" || bad "copy: $(cat "$F")"
[[ "$(mode "$F")" == 600 && "$(mode "$(dirname "$F")")" == 700 ]] && ok "the home is 0700 and the file 0600" || bad "modes"
grep -qx 'OPENCLAW_GATEWAY_TOKEN=tok-a' "$A/.env" && ok ".env's copy is left in place" || bad ".env changed"

# A second checkout (a regression worktree) with different .env values: the home wins.
B="$(repo b)"
printf 'OPENCLAW_GATEWAY_TOKEN=tok-b\nWEBUI_SECRET_KEY=key-b\nWEBUI_ADMIN_PASSWORD=pw-b\n' > "$B/.env"
init "$B" "$H"
grep -qx 'OPENCLAW_GATEWAY_TOKEN=tok-a' "$F" && ok "another checkout never overwrites the home" || bad "home overwritten"
# shellcheck source=lib/secrets-home.sh
source "$ROOT_DIR/scripts/lib/secrets-home.sh"
[[ "$(cd "$B" && RE_SECRETS_DIR="$H" oc_secret OPENCLAW_GATEWAY_TOKEN .env)" == tok-a ]] && ok "readers take the home over .env" || bad "oc_secret"

# A fresh checkout with placeholder .env values: generated in the home.
C="$(repo c)"; H2="$T/home2"
init "$C" "$H2"
F2="$H2/localOpenClawStack/secrets.env"
tok="$(sed -n 's/^OPENCLAW_GATEWAY_TOKEN=//p' "$F2")"
[[ "$tok" =~ ^[0-9a-f]{64}$ ]] && grep -qE '^WEBUI_ADMIN_PASSWORD=Aa1!' "$F2" && ok "placeholders become generated secrets in the home" || bad "generate: $(cat "$F2")"
init "$C" "$H2"
[[ "$(sed -n 's/^OPENCLAW_GATEWAY_TOKEN=//p' "$F2")" == "$tok" ]] && ok "a rerun keeps the generated values" || bad "regenerated"

# start.sh's migrate: a checkout's real .env values reach an empty home; placeholders do not.
D="$T/d"; mkdir -p "$D"; H3="$T/home3"
printf 'OPENCLAW_GATEWAY_TOKEN=tok-d\nWEBUI_SECRET_KEY=change-me-x\n' > "$D/.env"
(cd "$D" && RE_SECRETS_DIR="$H3" oc_secrets_migrate .env)
F3="$H3/localOpenClawStack/secrets.env"
grep -qx 'OPENCLAW_GATEWAY_TOKEN=tok-d' "$F3" && ! grep -q '^WEBUI_SECRET_KEY=' "$F3" && ok "migrate copies real values only" || bad "migrate: $(cat "$F3")"
printf 'OPENCLAW_GATEWAY_TOKEN=tok-other\n' > "$D/.env"
(cd "$D" && RE_SECRETS_DIR="$H3" oc_secrets_migrate .env)
grep -qx 'OPENCLAW_GATEWAY_TOKEN=tok-d' "$F3" && ok "migrate never overwrites the home" || bad "migrate overwrote"

# Nothing, anywhere: the reader is empty, not an error.
[[ -z "$(cd "$T" && RE_SECRETS_DIR="$T/none" oc_secret OPENCLAW_GATEWAY_TOKEN .env)" ]] && ok "no value reads as empty" || bad "empty read"

echo "$PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
