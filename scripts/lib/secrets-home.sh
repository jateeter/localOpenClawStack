#!/usr/bin/env bash
# secrets-home.sh — this stack's secrets, in the suite's one secrets home
# (owner, 2026-11-14; RealityEngine_CI scripts/lib/secrets-home.sh).
#
#   $RE_SECRETS_DIR/localOpenClawStack/secrets.env          (RE_SECRETS_DIR set)
#   $RE_INSTANCE_STATE_DIR/secrets/localOpenClawStack/secrets.env
#   ~/.reality-engine/secrets/localOpenClawStack/secrets.env  (the default)
#
# Holds OPENCLAW_GATEWAY_TOKEN, WEBUI_SECRET_KEY and WEBUI_ADMIN_PASSWORD: one
# place per machine, outside every repository, shared by every checkout.
# RealityEngine_CI's regression harness exports RE_SECRETS_DIR, so its
# cold-start worktrees read the same values as the operator's checkout. .env
# keeps operator settings; a value still in .env is the fallback for a checkout
# not yet migrated (scripts/init-secrets.sh copies it here and leaves it).
#
#   source scripts/lib/secrets-home.sh
#   oc_secrets_file             # prints the file
#   oc_secret <KEY> [env-file]  # the home's value, else the env file's
#   oc_secrets_export [env-file]  # export every key that has a value
#   oc_secrets_migrate [env-file] # copy .env's real values into an empty home

OC_SECRET_KEYS=(OPENCLAW_GATEWAY_TOKEN WEBUI_SECRET_KEY WEBUI_ADMIN_PASSWORD)

oc_secrets_file() {
  local base
  if [ -n "${RE_SECRETS_DIR:-}" ]; then base="$RE_SECRETS_DIR"
  else base="${RE_INSTANCE_STATE_DIR:-$HOME/.reality-engine}/secrets"; fi
  printf '%s/localOpenClawStack/secrets.env\n' "$base"
}

_oc_env_value() { # <file> <KEY>
  [ -f "$1" ] || return 0
  sed -n "s/^$2=//p" "$1" | tail -n 1 | tr -d "\"'"
}

oc_secret() {
  local key="$1" envf="${2:-.env}" value
  value="$(_oc_env_value "$(oc_secrets_file)" "$key")"
  [ -n "$value" ] || value="$(_oc_env_value "$envf" "$key")"
  printf '%s' "$value"
}

# Copy each key the home lacks from the env file, when it holds a real value
# (not a change-me-* placeholder), and verify it. The env file is not edited.
oc_secrets_migrate() {
  local envf="${1:-.env}" file key value
  file="$(oc_secrets_file)"
  ( umask 077; mkdir -p "$(dirname "$file")"; touch "$file" )
  chmod 700 "$(dirname "$file")"; chmod 600 "$file"
  for key in "${OC_SECRET_KEYS[@]}"; do
    [ -n "$(_oc_env_value "$file" "$key")" ] && continue
    value="$(_oc_env_value "$envf" "$key")"
    case "$value" in ''|change-me-*) continue ;; esac
    printf '%s=%s\n' "$key" "$value" >> "$file"
    [ "$(_oc_env_value "$file" "$key")" = "$value" ] || {
      echo "secrets-home: copying $key to $file did not verify" >&2; return 1; }
  done
}

oc_secrets_export() {
  local envf="${1:-.env}" key value
  for key in "${OC_SECRET_KEYS[@]}"; do
    value="$(oc_secret "$key" "$envf")"
    if [ -n "$value" ]; then export "$key=$value"; fi
  done
}
