#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=/dev/null
set -a; source .env; set +a

[[ "${NODE_IMAGE:-}" == *@sha256:* ]] || {
  echo "NODE_IMAGE must be digest-pinned; run ./scripts/update-versions.sh first." >&2
  exit 1
}

first="local/openclaw-reproduction:first"
second="local/openclaw-reproduction:second"
trap 'docker image rm -f "$first" "$second" >/dev/null 2>&1 || true' EXIT
common_args=(
  --no-cache
  --build-arg "NODE_IMAGE=$NODE_IMAGE"
  --build-arg "OPENCLAW_VERSION=$OPENCLAW_VERSION"
  -f Dockerfile.openclaw-gateway
  .
)

docker build --tag "$first" "${common_args[@]}"
docker build --tag "$second" "${common_args[@]}"

metadata_for() {
  docker image inspect -f \
    '{{index .Config.Labels "org.opencontainers.image.source"}}|{{index .Config.Labels "org.opencontainers.image.version"}}|{{json .Config.User}}|{{json .Config.Entrypoint}}|{{json .Config.Cmd}}' \
    "$1"
}

first_metadata="$(metadata_for "$first")"
second_metadata="$(metadata_for "$second")"

if [[ "$first_metadata" != "$second_metadata" ]]; then
  echo "Gateway rebuild produced different runtime metadata." >&2
  echo "first:  $first_metadata" >&2
  echo "second: $second_metadata" >&2
  exit 1
fi

for image in "$first" "$second"; do
  actual_version="$(docker run --rm "$image" openclaw --version | tr -d '\r')"
  [[ "$actual_version" == *"$OPENCLAW_VERSION"* ]] || {
    echo "$image reports unexpected OpenClaw version: $actual_version" >&2
    exit 1
  }
done

echo "Gateway rebuild reproduced the pinned runtime metadata and OpenClaw version."
