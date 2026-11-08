'use strict';
// Lane TLS for a Node process that cannot configure it itself: the native
// OpenClaw gateway (localOpenClawStack#92, RealityEngine_CI#668). Loaded with
// NODE_OPTIONS=--require=<this file> by scripts/native-gateway-run.sh, and only
// when the lane is on (RE_TLS_CERT set). With RE_TLS_CERT unset it does nothing.
//
// OpenClaw 2026.10.8 serves TLS 1.3 (gateway.tls), but it sets no cipher suites
// or key-exchange groups, so Node's defaults apply (AES-128, P-256 and FFDHE
// accepted). It also has no client-certificate setting except for MCP servers
// (mcp.servers.<name>.clientCert). This preload closes both, at the Node level
// rather than in OpenClaw's internals (owner decisions, 2026-11-08):
//
// 1. The profile (re-transport-2026.11) for every TLS context the process makes,
//    server and client: TLS 1.3 only, TLS_AES_256_GCM_SHA384 and
//    TLS_CHACHA20_POLY1305_SHA256, groups X25519MLKEM768, X25519, secp384r1.
// 2. The process's lane certificate (RE_TLS_CERT/RE_TLS_KEY) on outbound TLS to
//    internal peers only (the shared internal-peer rule, RealityEngine_CI#739:
//    loopback, RFC1918, link-local, ULA, single-label names, .localhost,
//    .internal, .local), and never when the caller brings its own certificate.
//    That is what lets the gateway reach the mTLS peers it has no setting for:
//    Ollama's TLS front and the browser sidecar's CDP. An external API never
//    sees the certificate.
//
// The lane CA is trusted through NODE_EXTRA_CA_CERTS (set by the run script),
// which Node reads before any code runs.

const fs = require('node:fs');
const net = require('node:net');
const tls = require('node:tls');

const PROFILE = {
  minVersion: 'TLSv1.3',
  ciphers: 'TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256',
  groups: 'X25519MLKEM768:X25519:secp384r1',
};

function isInternalPeer(host) {
  if (!host) return false;
  let h = String(host).toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (net.isIPv4(h)) {
    const [a, b] = h.split('.').map(Number);
    return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 169 && b === 254);
  }
  if (net.isIPv6(h)) {
    if (h === '::1') return true;
    const mapped = h.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
    if (mapped) return isInternalPeer(mapped[1]);
    return /^f[cd]/.test(h) || /^fe[89ab]/.test(h);
  }
  if (!h.includes('.')) return true;
  return h === 'localhost' || /\.(localhost|internal|local)$/.test(h);
}

// connect(options[, cb]) | connect(port[, host][, options][, cb]) | connect(path[, options][, cb])
function normalize(args) {
  const rest = [...args];
  const cb = typeof rest[rest.length - 1] === 'function' ? rest.pop() : undefined;
  let options = {};
  if (rest[0] !== null && typeof rest[0] === 'object') {
    options = { ...rest[0] };
  } else if (typeof rest[0] === 'number' || /^\d+$/.test(String(rest[0] ?? ''))) {
    options.port = Number(rest[0]);
    if (typeof rest[1] === 'string') options.host = rest[1];
    const last = rest[typeof rest[1] === 'string' ? 2 : 1];
    if (last && typeof last === 'object') options = { ...last, ...options };
  } else if (typeof rest[0] === 'string') {
    options.path = rest[0];
    if (rest[1] && typeof rest[1] === 'object') options = { ...rest[1], ...options };
  }
  return { options, cb };
}

function install(env = process.env) {
  if (!env.RE_TLS_CERT) return false;
  const cert = fs.readFileSync(env.RE_TLS_CERT);
  const key = fs.readFileSync(env.RE_TLS_KEY);

  tls.DEFAULT_MIN_VERSION = PROFILE.minVersion;
  tls.DEFAULT_MAX_VERSION = 'TLSv1.3';
  tls.DEFAULT_CIPHERS = PROFILE.ciphers;
  tls.DEFAULT_ECDH_CURVE = PROFILE.groups;

  const original = tls.connect;
  tls.connect = function laneConnect(...args) {
    const { options, cb } = normalize(args);
    const peer = options.servername || options.host || 'localhost';
    if (!options.cert && !options.pfx && !options.key && isInternalPeer(peer)) {
      options.cert = cert;
      options.key = key;
    }
    return cb ? original.call(this, options, cb) : original.call(this, options);
  };
  return true;
}

module.exports = { install, isInternalPeer, normalize, PROFILE };

install();
