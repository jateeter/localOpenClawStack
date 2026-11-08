#!/usr/bin/env bash
# Lane TLS for this stack (localOpenClawStack#92, RealityEngine_CI#668). Sourced.
#
# The lane is on when OPENCLAW_TLS_DIR names the directory RealityEngine_CI's
# startUniverse.sh links the lane's certificates into (links into the lane PKI,
# never copies; the CA's own directory is never there):
#
#   gateway/  chain.pem key.pem   the native gateway: serves TLS 1.3, and calls
#                                 the Ollama front and CDP with mTLS (the preload)
#   browser/  chain.pem key.pem   the browser sidecar's CDP proxy (nginx)
#   ca.crt    ca.crl              the lane CA and its revocation list
#
# With it unset nothing changes: the gateway serves http and ws on loopback.
# Owner decisions, 2026-11-08: the gateway cannot require client certificates
# (OpenClaw 2026.10.8 has no setting for it), an accepted deficiency compensated
# by its bearer token and loopback bind; Open WebUI is not on the lane yet.

oc_lane_on() { [[ -n "${OPENCLAW_TLS_DIR:-}" ]]; }

oc_lane_scheme() { if oc_lane_on; then echo https; else echo http; fi; }

# oc_lane_check: every file the lane needs exists. Prints what is missing.
oc_lane_check() {
  oc_lane_on || return 0
  local f missing=""
  for f in gateway/chain.pem gateway/key.pem browser/chain.pem browser/key.pem ca.crt ca.crl; do
    [[ -f "$OPENCLAW_TLS_DIR/$f" ]] || missing="$missing $f"
  done
  [[ -z "$missing" ]] || { echo "OPENCLAW_TLS_DIR=$OPENCLAW_TLS_DIR lacks:$missing" >&2; return 1; }
}

# oc_curl ARGS...: curl, speaking the lane's TLS 1.3 with the gateway's
# certificate when the lane is on. macOS's /usr/bin/curl (SecureTransport) cannot
# speak TLS 1.3, so the lane needs Homebrew's curl.
oc_curl() {
  if ! oc_lane_on; then curl "$@"; return; fi
  local c
  for c in /opt/homebrew/opt/curl/bin/curl /usr/local/opt/curl/bin/curl "$(command -v curl)"; do
    [[ -x "$c" ]] && "$c" -V 2>/dev/null | head -1 | grep -qE "OpenSSL|BoringSSL|GnuTLS|rustls|wolfSSL" && break
    c=""
  done
  [[ -n "$c" ]] || { echo "no TLS 1.3-capable curl (install Homebrew curl)" >&2; return 1; }
  "$c" --tlsv1.3 --cacert "$OPENCLAW_TLS_DIR/ca.crt" \
    --cert "$OPENCLAW_TLS_DIR/gateway/chain.pem" --key "$OPENCLAW_TLS_DIR/gateway/key.pem" "$@"
}
