#!/bin/bash
# The native gateway's LaunchAgent program (scripts/native-gateway.sh writes the
# LaunchAgent). launchd runs it with /bin/bash, macOS's 3.2, so it stays plain.
#
# It reads the gateway token from this stack's .env when the gateway starts, so the
# token is never stored in the LaunchAgent plist, and passes on nothing else from
# .env: the gateway does not need the Open WebUI or other secrets kept there.
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
token="$(sed -n 's/^OPENCLAW_GATEWAY_TOKEN=//p' "$ROOT_DIR/.env" | tail -1 | tr -d "\"'")"
if [ -z "$token" ]; then
  echo "native-gateway-run: OPENCLAW_GATEWAY_TOKEN is not set in $ROOT_DIR/.env" >&2
  exit 1
fi

export OPENCLAW_GATEWAY_TOKEN="$token"
export OPENCLAW_GATEWAY_MODE=local
export OLLAMA_API_KEY="${OLLAMA_API_KEY:-ollama-local}"
exec "${OPENCLAW_BIN:-openclaw}" gateway --port "${OPENCLAW_GATEWAY_PORT:-18789}" --bind loopback
