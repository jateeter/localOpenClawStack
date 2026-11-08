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

# Lane TLS (localOpenClawStack#92): the gateway serves the lane's certificate
# (harden-config.sh writes gateway.tls), and scripts/lib/lane-tls-preload.cjs
# holds every TLS context to the profile and presents the gateway's certificate
# to internal mTLS peers (the Ollama front, CDP). The lane CA is trusted from the
# start, before any code runs.
if [ -n "${OPENCLAW_TLS_DIR:-}" ]; then
  export RE_TLS_CERT="$OPENCLAW_TLS_DIR/gateway/chain.pem"
  export RE_TLS_KEY="$OPENCLAW_TLS_DIR/gateway/key.pem"
  export NODE_EXTRA_CA_CERTS="$OPENCLAW_TLS_DIR/ca.crt"
  export NODE_OPTIONS="--require=$ROOT_DIR/scripts/lib/lane-tls-preload.cjs${NODE_OPTIONS:+ $NODE_OPTIONS}"
fi

export OPENCLAW_GATEWAY_TOKEN="$token"
export OPENCLAW_GATEWAY_MODE=local
export OLLAMA_API_KEY="${OLLAMA_API_KEY:-ollama-local}"
exec "${OPENCLAW_BIN:-openclaw}" gateway --port "${OPENCLAW_GATEWAY_PORT:-18789}" --bind loopback
