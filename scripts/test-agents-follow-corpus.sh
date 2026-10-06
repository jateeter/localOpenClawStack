#!/usr/bin/env bash
# OpenClaw's agents follow the corpus the engines loaded (owner rule 2026-10-06).
#
# Exercises agent-profile.sh manifest:<file> and verify-agents-match-corpus.py
# against fake engines: local HTTP servers answering GET /api/machines/json/list
# with a chosen corpus, listed in a scratch instance registry. Needs the sibling
# RealityEngine_CI (corpus manifests) and RealityEngine_Machines checkouts.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CI_CONFIG="$ROOT_DIR/../RealityEngine_CI/config"
TMP="$(mktemp -d)"
PIDS="$TMP/pids"
cleanup() {
  local p
  if [[ -f "$PIDS" ]]; then while read -r p; do kill "$p" 2>/dev/null || true; done < "$PIDS"; fi
  rm -rf "$TMP"
}
trap cleanup EXIT

PASS=0; FAIL=0
check() { if [[ "$1" == "$2" ]]; then echo "  PASS: $3"; PASS=$((PASS+1)); else echo "  FAIL: $3 (expected '$2', got '$1')"; FAIL=$((FAIL+1)); fi; }

[[ -d "$CI_CONFIG" ]] || { echo "  SKIP: no sibling RealityEngine_CI checkout"; exit 0; }

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

# A fake engine answering json/list with the entries of a manifest.
engine() {  # <manifest> -> port
  local port; port="$(free_port)"
  python3 - "$port" "$1" >/dev/null 2>&1 <<'PY' &
import http.server, json, sys
port, manifest = int(sys.argv[1]), sys.argv[2]
rows = [{"relFile": l.split("#", 1)[0].strip()} for l in open(manifest) if l.split("#", 1)[0].strip()]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"machines": rows}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
  echo "$!" >> "$PIDS"
  local n=0; until curl -sf "http://127.0.0.1:$port/" >/dev/null 2>&1 || [[ $n -ge 50 ]]; do sleep 0.1; n=$((n+1)); done
  echo "$port"
}
registry() {  # <port|dead>... -> writes $TMP/registry.json
  local i=0 p json='{"instances":['
  for p in "$@"; do
    [[ "$p" == dead ]] && p="$(free_port)"
    [[ $i -gt 0 ]] && json+=","
    json+="{\"id\":\"e-$i\",\"re_url\":\"http://127.0.0.1:$p\"}"; i=$((i+1))
  done
  printf '%s]}\n' "$json" > "$TMP/registry.json"
}
verify() {  # <deployed index> -> "rc|output"
  local out rc=0
  out="$(python3 "$ROOT_DIR/scripts/verify-agents-match-corpus.py" --deployed-index "$1" --registry-file "$TMP/registry.json" 2>&1)" || rc=$?
  printf '%s|%s' "$rc" "$out"
}

SD="$CI_CONFIG/standard-deployment-corpus.txt"
REG="$CI_CONFIG/regression-corpus.txt"
FIX="$CI_CONFIG/arbiter-fixture-corpus.txt"
SD_INDEX="$("$ROOT_DIR/scripts/agent-profile.sh" "manifest:$SD")"
REG_INDEX="$("$ROOT_DIR/scripts/agent-profile.sh" regression)"
FIX_INDEX="$("$ROOT_DIR/scripts/agent-profile.sh" "manifest:$FIX")"

echo "agent-profile.sh manifest:<file>"
check "$(jq .total "$SD_INDEX")" 12 "standard-deployment deploys its own 12 agents, not the regression profile's 15"
check "$(jq -r '.agents[].agentId' "$("$ROOT_DIR/scripts/agent-profile.sh" "manifest:$REG")" | sort | tr '\n' ' ')" \
      "$(jq -r '.agents[].agentId' "$REG_INDEX" | sort | tr '\n' ' ')" "the regression manifest yields exactly the regression profile"
check "$(jq -r .manifest "$SD_INDEX")" "$SD" "the index records the manifest it came from"

echo "verify-agents-match-corpus.py"
P1="$(engine "$SD")"; P2="$(engine "$SD")"; P3="$(engine "$SD")"; P4="$(engine "$REG")"; PF="$(engine "$FIX")"
rm -f "$TMP/registry.json"
check "$(verify "$SD_INDEX" | cut -d'|' -f1)" 0 "no instance registry: no running universe, not a failure"
registry "$P1" "$P2" "$P3"
check "$(verify "$SD_INDEX" | cut -d'|' -f1)" 0 "engines on standard-deployment, OpenClaw on its 12 agents: passes"
r="$(verify "$REG_INDEX")"
check "${r%%|*}" 1 "engines on standard-deployment, OpenClaw on the regression profile: fails"
check "$(grep -c 'not in the deployed corpus 3' <<<"$r")" 1 "and names the 3 agents the deployed corpus does not have"
registry "$P1" "$P2" "$P4"
r="$(verify "$SD_INDEX")"
check "${r%%|*}" 1 "engines disagree on the corpus: fails"
check "$(grep -c 'e-0=.*e-1=.*e-2=' <<<"$r")" 1 "and reports every engine, none as the reference"
registry "$P1" "$P2" dead
r="$(verify "$SD_INDEX")"
check "${r%%|*}" 1 "a listed engine that does not answer while others do: fails"
registry dead dead dead
check "$(verify "$SD_INDEX" | cut -d'|' -f1)" 0 "every listed engine silent: no running universe, not a failure"
registry "$PF" "$PF" "$PF"
check "$(verify "$FIX_INDEX" | cut -d'|' -f1)" 0 "an arbiter-fixture corpus follows its own agents"

echo ""
echo "  $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
