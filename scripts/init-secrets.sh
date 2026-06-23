#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[[ -f .env ]] || cp .env.example .env
OWNER="${LOCAL_OWNER:-johnt}"
GROUP="${LOCAL_GROUP:-staff}"

upsert_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ "^" key "=" { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' .env > "$tmp"
  mv "$tmp" .env
}

current_value() {
  sed -n "s/^$1=//p" .env | tail -n 1
}

ensure_secret() {
  local key="$1" value
  value="$(current_value "$key")"
  if [[ -z "$value" || "$value" == change-me-* ]]; then
    upsert_env "$key" "$(openssl rand -hex 32)"
  fi
}

ensure_password() {
  local key="$1" value
  value="$(current_value "$key")"
  if [[ -z "$value" || "$value" == change-me-* ]]; then
    # Meets Open WebUI's default uppercase/lowercase/digit/symbol policy.
    upsert_env "$key" "Aa1!$(openssl rand -hex 28)"
  fi
}

ensure_secret OPENCLAW_GATEWAY_TOKEN
ensure_secret WEBUI_SECRET_KEY
ensure_password WEBUI_ADMIN_PASSWORD

email="$(current_value WEBUI_ADMIN_EMAIL)"
if [[ -z "$email" || "$email" == "admin@example.com" ]]; then
  upsert_env WEBUI_ADMIN_EMAIL "$(id -un)@localhost"
fi

name="$(current_value WEBUI_ADMIN_NAME)"
if [[ -z "$name" || "$name" == "Admin" ]]; then
  upsert_env WEBUI_ADMIN_NAME "$(id -un)"
fi

upsert_env LOCAL_OWNER "$OWNER"
upsert_env LOCAL_GROUP "$GROUP"
upsert_env LOCAL_UID "$(id -u "$OWNER")"
upsert_env LOCAL_GID "$(id -g "$OWNER")"

chown "$OWNER:$GROUP" .env
chmod 600 .env
for path in openclaw openwebui-data browser-config; do
  [[ -e "$path" ]] || mkdir -p "$path"
  chown -R "$OWNER:$GROUP" "$path"
  find "$path" -type d -exec chmod 700 {} +
  find "$path" -type f -exec chmod 600 {} +
done
[[ -f openclaw/claude.md ]] && chmod 644 openclaw/claude.md

echo "Secrets initialized in owner-only .env for WEBUI_ADMIN_EMAIL=$(current_value WEBUI_ADMIN_EMAIL)."
