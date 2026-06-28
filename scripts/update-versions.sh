#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

[[ -f .env ]] || { echo ".env is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

next_env="$(mktemp)"
cp .env "$next_env"
trap 'rm -f "$next_env"' EXIT

upsert_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ "^" key "=" { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$next_env" > "$tmp"
  mv "$tmp" "$next_env"
}

latest_release() {
  local repo="$1" tag
  tag="$(curl --fail --silent --show-error --retry 3 \
    --connect-timeout 10 --max-time 30 \
    "https://api.github.com/repos/${repo}/releases/latest" | \
    jq -er 'select(.draft == false and .prerelease == false) | .tag_name')"
  [[ "$tag" != *-* ]] || { echo "Refusing prerelease tag: $tag" >&2; exit 1; }
  printf '%s' "$tag"
}

latest_npm_version() {
  local package="$1" version
  version="$(curl --fail --silent --show-error --retry 3 \
    --connect-timeout 10 --max-time 30 \
    "https://registry.npmjs.org/${package}/latest" | \
    jq -er '.version')"
  [[ "$version" != *-* ]] || { echo "Refusing prerelease version: $version" >&2; exit 1; }
  printf '%s' "$version"
}

pin_image() {
  local key="$1" ref="$2" digest
  docker pull "$ref"
  digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$ref")"
  [[ "$digest" == *@sha256:* ]] || { echo "Could not resolve immutable digest for $ref" >&2; exit 1; }
  upsert_env "$key" "$digest"
}

openclaw_version="$(latest_npm_version openclaw)"
openwebui_tag="$(latest_release open-webui/open-webui)"

upsert_env OPENCLAW_VERSION "$openclaw_version"
pin_image NODE_IMAGE "node:24-bookworm-slim"
pin_image OPEN_WEBUI_IMAGE "ghcr.io/open-webui/open-webui:${openwebui_tag}"
pin_image BROWSER_IMAGE "coollabsio/openclaw-browser:0.3.0"

mv "$next_env" .env
chmod 600 .env
trap - EXIT

echo "Pinned OpenClaw ${openclaw_version}, Node base, Open WebUI ${openwebui_tag}, and browser image digests."
