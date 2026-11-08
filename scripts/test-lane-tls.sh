#!/usr/bin/env bash
# =============================================================================
# test-lane-tls.sh — the stack on the lane's TLS (localOpenClawStack#92).
#
#   1. harden-config.sh writes gateway.tls from OPENCLAW_TLS_DIR (the lane's
#      certificate, never a generated one) and https origins, removes it again
#      when the lane is off, and `openclaw config validate` accepts it (skipped,
#      loudly, without openclaw on PATH).
#   2. native-gateway.sh's LaunchAgent carries OPENCLAW_TLS_DIR only on the lane,
#      and native-gateway-run.sh hands the gateway the preload, its certificate
#      and the lane CA (a stub openclaw prints what it was given).
#   3. lane-tls-preload.cjs: the internal-peer rule; a server built as the
#      gateway builds its own is held to the profile (AES-128, P-256, FFDHE and
#      TLS 1.2 refused); outbound TLS presents the certificate to an internal
#      mTLS peer, and not when the caller brings its own.
#   4. docker-compose.tls.yml is valid and mounts the CDP proxy config.
#
# Sandboxed: a throwaway CA from the openssl CLI in a temp dir, scratch state and
# plist paths, loopback-only test servers on ephemeral ports it stops itself.
# Nothing is loaded into launchd, no gateway or container starts, and the
# operator's openclaw/, ~/.openclaw and ~/.realityengine are not touched.
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
command -v jq >/dev/null || { echo "[FAIL] jq is required" >&2; exit 1; }
NODE="$(command -v node || true)"
[[ -x /opt/homebrew/bin/node ]] && NODE=/opt/homebrew/bin/node
[[ -n "$NODE" ]] || { echo "[FAIL] node is required" >&2; exit 1; }

T="$(mktemp -d)"
PIDS=()
cleanup() { for p in "${PIDS[@]+"${PIDS[@]}"}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$T"; }
trap cleanup EXIT
fails=0
ok()   { echo "  ok   $1"; }
bad()  { echo "  FAIL $1"; fails=$((fails + 1)); }
check() { if "${@:2}" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

echo "lane TLS"

# ── a throwaway lane CA and the OPENCLAW_TLS_DIR layout ─────────────────────
P="$T/pki"; mkdir -p "$P"
ossl() { openssl "$@" >/dev/null 2>&1; }
ossl ecparam -name secp384r1 -genkey -noout -out "$P/ca.key"
ossl req -x509 -new -key "$P/ca.key" -sha384 -days 2 -subj "/CN=test lane CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" -out "$P/ca.crt"
printf '[ca]\ndefault_ca=c\n[c]\ndatabase=%s/index\ncrlnumber=%s/crlnumber\ndefault_md=sha384\ndefault_crl_days=2\n' "$P" "$P" > "$P/ca.cnf"
: > "$P/index"; echo 01 > "$P/crlnumber"
ossl ca -config "$P/ca.cnf" -gencrl -keyfile "$P/ca.key" -cert "$P/ca.crt" -out "$P/ca.crl"
leaf() { # name
  ossl ecparam -name secp384r1 -genkey -noout -out "$P/$1.key"
  ossl req -new -key "$P/$1.key" -subj "/CN=$1" -out "$P/$1.csr"
  printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth,clientAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n' > "$P/$1.ext"
  ossl x509 -req -in "$P/$1.csr" -CA "$P/ca.crt" -CAkey "$P/ca.key" -CAcreateserial -sha384 -days 2 -extfile "$P/$1.ext" -out "$P/$1.crt"
  cat "$P/$1.crt" "$P/ca.crt" > "$P/$1.chain"
}
leaf gateway; leaf browser; leaf peer
TLS="$T/tls"
for n in gateway browser; do mkdir -p "$TLS/$n"; cp "$P/$n.chain" "$TLS/$n/chain.pem"; cp "$P/$n.key" "$TLS/$n/key.pem"; done
cp "$P/ca.crt" "$P/ca.crl" "$TLS/"
[[ -f "$P/ca.crl" ]] && ok "throwaway lane CA, its CRL and the gateway/browser certificates" || bad "test PKI"

# ── 1. harden-config.sh ─────────────────────────────────────────────────────
E="$T/native"; mkdir -p "$E"
gen() { env OPENCLAW_SCHEMA=entries OPENCLAW_STATE_DIR="$E" OPENCLAW_HOME_DIR="$E" \
  OPENCLAW_GATEWAY_BIND=loopback OLLAMA_BASE_URL=https://127.0.0.1:11443 "$@" "$ROOT_DIR/scripts/harden-config.sh"; }
gen OPENCLAW_TLS_DIR="$TLS" >/dev/null
# Three real agents, so the config is one a gateway would run (openclaw's own
# validator wants at least one).
INDEX="$T/INDEX.json"
REG="$(OPENCLAW_AGENT_PROFILE=regression "$ROOT_DIR/scripts/agent-profile.sh" regression 2>/dev/null | tail -1)"
jq '.agents |= .[0:3] | .total = 3' "$REG" > "$INDEX"
env OPENCLAW_SCHEMA=entries OPENCLAW_STATE_DIR="$E" OPENCLAW_HOME_DIR="$E" OPENCLAW_AGENT_INDEX_PATH="$INDEX" \
  "$ROOT_DIR/scripts/sync-machine-agents.sh" >/dev/null 2>&1
CFG="$E/openclaw.json"
check "on: gateway.tls names the lane certificate and CA, and never generates one" \
  jq -e --arg d "$TLS" '.gateway.tls == {enabled: true, autoGenerate: false, certPath: ($d + "/gateway/chain.pem"), keyPath: ($d + "/gateway/key.pem"), caPath: ($d + "/ca.crt")}' "$CFG"
check "on: the Control UI's allowed origins are https" \
  jq -e '.gateway.controlUi.allowedOrigins | all(startswith("https://"))' "$CFG"
OC="$(command -v openclaw || true)"
if [[ -n "$OC" ]]; then
  if PATH="$(dirname "$OC"):$PATH" OPENCLAW_STATE_DIR="$E" OPENCLAW_CONFIG_PATH="$CFG" openclaw config validate >"$T/validate.out" 2>&1; then
    ok "on: openclaw $(openclaw --version 2>/dev/null | awk '{print $2}') accepts the gateway.tls block"
  else
    bad "on: openclaw config validate rejects the gateway.tls block"; sed 's/^/       /' "$T/validate.out" | head -10
  fi
else
  echo "  SKIP openclaw is not on PATH: its own config validate was not run"
fi
mv "$TLS/ca.crl" "$T/ca.crl.held"
if gen OPENCLAW_TLS_DIR="$TLS" >/dev/null 2>&1; then bad "on: a lane missing its CRL was accepted"; else ok "on: a lane missing a file is refused"; fi
mv "$T/ca.crl.held" "$TLS/ca.crl"
gen >/dev/null
check "off: the gateway.tls block is removed, and the origins are no longer https" \
  jq -e '(.gateway | has("tls") | not) and (.gateway.controlUi.allowedOrigins | all(startswith("https:") | not))' "$CFG"

# ── 2. the LaunchAgent and its run script ───────────────────────────────────
STUB="$T/bin"; mkdir -p "$STUB"
cat > "$STUB/openclaw" <<'SH'
#!/bin/bash
echo "RE_TLS_CERT=${RE_TLS_CERT:-}"
echo "RE_TLS_KEY=${RE_TLS_KEY:-}"
echo "NODE_EXTRA_CA_CERTS=${NODE_EXTRA_CA_CERTS:-}"
echo "NODE_OPTIONS=${NODE_OPTIONS:-}"
SH
chmod +x "$STUB/openclaw"
ENVF="$T/repo-env"; printf 'OPENCLAW_GATEWAY_TOKEN=test-token\n' > "$ENVF"
run_gw() { # copies the run script beside a .env, as the LaunchAgent runs it
  mkdir -p "$T/repo/scripts/lib"; cp "$ROOT_DIR/scripts/native-gateway-run.sh" "$T/repo/scripts/"; cp "$ENVF" "$T/repo/.env"
  env -i HOME="$T" PATH="/usr/bin:/bin" OPENCLAW_BIN="$STUB/openclaw" "$@" /bin/bash "$T/repo/scripts/native-gateway-run.sh"
}
out="$(run_gw OPENCLAW_TLS_DIR="$TLS")"
grep -qx "RE_TLS_CERT=$TLS/gateway/chain.pem" <<<"$out" && grep -qx "NODE_EXTRA_CA_CERTS=$TLS/ca.crt" <<<"$out" \
  && grep -q "^NODE_OPTIONS=--require=$T/repo/scripts/lib/lane-tls-preload.cjs" <<<"$out" \
  && ok "on: the run script hands the gateway its certificate, the lane CA and the preload" || bad "on: run script env: $out"
out="$(run_gw)"
grep -qx "RE_TLS_CERT=" <<<"$out" && grep -qx "NODE_OPTIONS=" <<<"$out" \
  && ok "off: the run script adds nothing" || bad "off: run script env: $out"
render() { env OPENCLAW_NATIVE_PLIST="$T/agent.plist" OPENCLAW_BIN="$STUB/openclaw" OPENCLAW_NODE="$NODE" \
  OPENCLAW_VERSION=0.0.0 "$@" "$ROOT_DIR/scripts/native-gateway.sh" render >/dev/null; }
render OPENCLAW_TLS_DIR="$TLS"
grep -q "<key>OPENCLAW_TLS_DIR</key><string>$TLS</string>" "$T/agent.plist" && plutil -lint "$T/agent.plist" >/dev/null 2>&1 \
  && ok "on: the LaunchAgent carries OPENCLAW_TLS_DIR and is a valid plist" || bad "on: LaunchAgent"
render
! grep -q OPENCLAW_TLS_DIR "$T/agent.plist" && ok "off: the LaunchAgent does not" || bad "off: the LaunchAgent names OPENCLAW_TLS_DIR"

# ── 3. the preload ──────────────────────────────────────────────────────────
PRELOAD="$ROOT_DIR/scripts/lib/lane-tls-preload.cjs"
"$NODE" -e '
  const { isInternalPeer } = require(process.argv[1]);
  const yes = ["127.0.0.1", "localhost", "::1", "[::1]", "10.1.2.3", "172.20.0.5", "192.168.1.194", "169.254.1.1",
               "fd12::1", "fe80::1", "host.docker.internal", "ollama.localhost", "mac.local", "api"];
  const no = ["8.8.8.8", "172.32.0.1", "api.openai.com", "github.com", "2001:db8::1", "ollama.com"];
  const wrong = [...yes.filter(h => !isInternalPeer(h)), ...no.filter(h => isInternalPeer(h))];
  if (wrong.length) { console.error("misclassified:", wrong.join(" ")); process.exit(1); }' "$PRELOAD" \
  && ok "the internal-peer rule (RealityEngine_CI#739)" || bad "the internal-peer rule"

cat > "$T/srv.mjs" <<JS
import tls from "node:tls"; import fs from "node:fs";
const mtls = process.argv[2] === "mtls";
const s = tls.createServer({ cert: fs.readFileSync("$TLS/gateway/chain.pem"), key: fs.readFileSync("$TLS/gateway/key.pem"),
  ca: fs.readFileSync("$TLS/ca.crt"), minVersion: "TLSv1.3", requestCert: mtls, rejectUnauthorized: mtls },
  c => c.end((c.getPeerCertificate().subject?.CN || "anonymous") + "\n"));
s.listen(0, "127.0.0.1", () => console.log(s.address().port));
JS
start_srv() { # mode -> port on stdout; the server's own env decides the preload
  local log="$T/srv-$1-$RANDOM.log"
  env "${@:2}" "$NODE" "$T/srv.mjs" "$1" > "$log" 2>&1 &
  PIDS+=("$!")
  for _ in $(seq 1 50); do [[ -s "$log" ]] && break; sleep 0.1; done
  head -1 "$log"
}
LANE_ENV=(RE_TLS_CERT="$TLS/gateway/chain.pem" RE_TLS_KEY="$TLS/gateway/key.pem" NODE_OPTIONS="--require=$PRELOAD")
port="$(start_srv plain "${LANE_ENV[@]}")"
hs() { echo | perl -e 'alarm 5; exec @ARGV' openssl s_client -connect "127.0.0.1:$port" -CAfile "$TLS/ca.crt" "$@" 2>&1 | grep -c "Cipher is TLS_" || true; }
[[ "$(hs -tls1_3)" == 1 ]] && ok "server: TLS 1.3 with a profile suite is accepted" || bad "server: profile handshake failed"
refused=""
[[ "$(hs -ciphersuites TLS_AES_128_GCM_SHA256)" == 0 ]] || refused="$refused aes128"
[[ "$(hs -groups prime256v1)" == 0 ]] || refused="$refused p256"
[[ "$(hs -groups ffdhe2048)" == 0 ]] || refused="$refused ffdhe"
[[ "$(hs -tls1_2)" == 0 ]] || refused="$refused tls1.2"
[[ -z "$refused" ]] && ok "server: AES-128, P-256, FFDHE and TLS 1.2 are refused" || bad "server: accepted:$refused"

mport="$(start_srv mtls)"
cat > "$T/cli.mjs" <<JS
import tls from "node:tls"; import fs from "node:fs";
const own = process.argv[2] === "own";
const opts = { host: "127.0.0.1", port: Number(process.argv[3]), servername: "localhost" };
if (own) Object.assign(opts, { cert: fs.readFileSync("$P/peer.chain"), key: fs.readFileSync("$P/peer.key") });
const s = tls.connect(opts, () => {}); s.setEncoding("utf8");
s.on("data", d => { process.stdout.write(d); s.end(); }); s.on("error", e => { console.log("ERR " + e.code); });
JS
got="$(env NODE_EXTRA_CA_CERTS="$TLS/ca.crt" "${LANE_ENV[@]}" "$NODE" "$T/cli.mjs" lane "$mport")"
[[ "$got" == gateway ]] && ok "client: the lane certificate is presented to an internal mTLS peer" || bad "client: peer saw '$got'"
got="$(env NODE_EXTRA_CA_CERTS="$TLS/ca.crt" "${LANE_ENV[@]}" "$NODE" "$T/cli.mjs" own "$mport")"
[[ "$got" == peer ]] && ok "client: a caller's own certificate is left alone" || bad "client: peer saw '$got'"
got="$(NODE_EXTRA_CA_CERTS="$TLS/ca.crt" "$NODE" "$T/cli.mjs" lane "$mport")"
[[ "$got" == ERR* ]] && ok "client: without the preload the same peer refuses it" || bad "client: no preload, peer saw '$got'"

# ── 4. the compose overlay ──────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  COMPOSE_FILE=docker-compose.yml:docker-compose.tls.yml OPENCLAW_TLS_DIR="$TLS" docker compose config --format json > "$T/compose.json" 2>"$T/compose.err" \
    && jq -e '[.services.browser.volumes[] | .target] | index("/etc/nginx/cdp-proxy.conf") and index("/run/re-tls/browser")' "$T/compose.json" >/dev/null \
    && jq -e '.services | has("open-webui") | not' "$T/compose.json" >/dev/null \
    && ok "overlay: browser mounts the TLS CDP proxy and the lane certificate; Open WebUI is left out" \
    || { bad "overlay"; head -5 "$T/compose.err"; }
else
  echo "  SKIP docker is not installed: the overlay was not checked"
fi

echo
if [[ "$fails" -eq 0 ]]; then echo "all ok"; else echo "$fails failure(s)"; exit 1; fi
