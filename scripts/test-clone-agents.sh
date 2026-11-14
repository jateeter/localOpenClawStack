#!/usr/bin/env bash
# Per-clone agents (scripts/clone-agents.mjs; localOpenClawStack#91): concurrent
# loads and unloads leave openclaw.json consistent, an unload leaves nothing of
# the agent behind, and every rewrite is adopted as the config baseline.
#
# Self-contained: a scratch state directory with a minimal config, for each
# schema (agents.entries and agents.list). No gateway, no Docker.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT_DIR/scripts/clone-agents.mjs"
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }
FAILED=0
check() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; FAILED=1; fi; }

run_schema() {
  local schema="$1" dir
  dir="$(mktemp -d)"
  trap 'rm -rf "$dir"' RETURN
  local tpl_ws="$dir/workspaces/machine-behaviors/ifrs-sec01-generalpurposefs-verifier"
  mkdir -p "$tpl_ws" "$dir/agents/ifrs-sec01-generalpurposefs-verifier/agent" "$dir/logs"
  echo '{"agentId":"ifrs-sec01-generalpurposefs-verifier"}' > "$tpl_ws/oc-agent.json"
  echo '# IFRS_Sec01_GeneralPurposeFS_Verifier' > "$tpl_ws/AGENTS.md"
  echo '{"providers":{},"selected":{"model":"ollama/nemotron-3-nano:4b"}}' > "$dir/agents/ifrs-sec01-generalpurposefs-verifier/agent/models.json"
  echo '{"agents":[{"agentId":"ifrs-sec01-generalpurposefs-verifier","machineId":"IFRS_Sec01_GeneralPurposeFS_Verifier"}]}' \
    > "$dir/workspaces/machine-behaviors/INDEX.json"
  local tpl main
  tpl="{\"name\":\"IFRS Sec01\",\"workspace\":\"/home/node/.openclaw/workspaces/machine-behaviors/ifrs-sec01-generalpurposefs-verifier\",\"agentDir\":\"/home/node/.openclaw/agents/ifrs-sec01-generalpurposefs-verifier/agent\",\"model\":{\"primary\":\"ollama/nemotron-3-nano:4b\"},\"sandbox\":{\"mode\":\"all\"}}"
  main='{"name":"Main","workspace":"/home/node/.openclaw/workspace","agentDir":"/home/node/.openclaw/agents/main/agent"}'
  if [[ "$schema" == entries ]]; then
    echo "{\"gateway\":{\"mode\":\"local\"},\"agents\":{\"entries\":{\"main\":$main,\"ifrs-sec01-generalpurposefs-verifier\":$tpl}}}" | jq . > "$dir/openclaw.json"
  else
    echo "{\"gateway\":{\"mode\":\"local\"},\"agents\":{\"list\":[$(jq -c '. + {id:"main"}' <<<"$main"),$(jq -c '. + {id:"ifrs-sec01-generalpurposefs-verifier"}' <<<"$tpl")]}}" | jq . > "$dir/openclaw.json"
  fi
  export OPENCLAW_STATE_DIR="$dir" OPENCLAW_HOME_DIR=/home/node/.openclaw
  unset OPENCLAW_CONFIG_PATH

  # 12 loads at once, half by machine id and half by agent id.
  local i pids=()
  for i in $(seq 1 12); do
    if (( i % 2 )); then t=machine-ifrs-sec01-generalpurposefs-verifier; else t=ifrs-sec01-generalpurposefs-verifier; fi
    node "$CLI" load --template "$t" --clone "machine-ifrs-sec01-generalpurposefs-verifier.cpp-1.env-$i" > "$dir/load-$i.json" & pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p"; done
  check "$schema: 12 concurrent loads leave a valid config with every clone" \
    "[[ \$(node '$CLI' list | jq '.clones | length') == 12 ]] && jq -e . '$dir/openclaw.json' >/dev/null"
  check "$schema: each clone has its workspace (the template's files) and state" \
    "for i in \$(seq 1 12); do a=\$(jq -r .agentId '$dir/load-'\$i.json); [[ -f '$dir/workspaces/clones/'\$a/AGENTS.md && -f '$dir/workspaces/clones/'\$a/CLONE.md && -f '$dir/agents/'\$a/agent/models.json ]] || exit 1; done"
  check "$schema: every clone agent is limited to session_status" \
    "node -e \"const c=require('$dir/openclaw.json');const a=c.agents.entries?Object.entries(c.agents.entries).map(([id,v])=>({id,...v})):c.agents.list;const cl=a.filter(x=>x.id.startsWith('clone-'));process.exit(cl.length===12&&cl.every(x=>['group:runtime','group:fs','group:web','group:plugins'].every(g=>(x.tools?.deny||[]).includes(g))&&(x.tools?.allow||[]).length===1&&x.tools.allow[0]==='session_status')?0:1)\""
  check "$schema: the template and main are untouched" \
    "[[ \$(node -e \"const c=require('$dir/openclaw.json');const a=c.agents.entries?Object.keys(c.agents.entries):c.agents.list.map(x=>x.id);console.log(a.filter(x=>!x.startsWith('clone-')).sort().join(','))\") == 'ifrs-sec01-generalpurposefs-verifier,main' ]]"

  # Unloads of 1..6 interleaved with loads of 13..18.
  pids=()
  for i in $(seq 1 6); do
    node "$CLI" unload --clone "machine-ifrs-sec01-generalpurposefs-verifier.cpp-1.env-$i" > "$dir/unload-$i.json" & pids+=($!)
    node "$CLI" load --template ifrs-sec01-generalpurposefs-verifier --clone "machine-ifrs-sec01-generalpurposefs-verifier.lsp-1.env-$((i + 12))" > /dev/null & pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p"; done
  check "$schema: interleaved unloads and loads leave exactly the 12 expected clones" \
    "[[ \$(node '$CLI' list | jq '.clones | length') == 12 ]] && jq -e . '$dir/openclaw.json' >/dev/null"
  check "$schema: an unloaded agent leaves no entry, workspace or state" \
    "for i in \$(seq 1 6); do a=\$(jq -r .agentId '$dir/unload-'\$i.json); [[ \$(jq -r .unloaded '$dir/unload-'\$i.json) == true && ! -e '$dir/workspaces/clones/'\$a && ! -e '$dir/agents/'\$a ]] && ! grep -q \"\$a\" '$dir/openclaw.json' || exit 1; done"
  check "$schema: a second unload is a no-op; a second load returns the same agent" \
    "[[ \$(node '$CLI' unload --clone machine-ifrs-sec01-generalpurposefs-verifier.cpp-1.env-1 | jq .unloaded) == false && \$(node '$CLI' load --template ifrs-sec01-generalpurposefs-verifier --clone machine-ifrs-sec01-generalpurposefs-verifier.cpp-1.env-7 | jq .loaded) == false ]]"
  check "$schema: purge removes the agent's sandbox workspaces (as OpenClaw protects them), and only those" \
    "c=machine-ifrs-sec01-generalpurposefs-verifier.cpp-1.env-8; a=\$(jq -r .agentId '$dir/load-8.json'); mkdir -p '$dir/sandboxes/workspace-aaa/skills' '$dir/sandboxes/workspace-bbb'; cp '$dir/workspaces/clones/'\$a/AGENTS.md '$dir/sandboxes/workspace-aaa/AGENTS.md'; chmod 500 '$dir/sandboxes/workspace-aaa/skills'; if [[ \$(uname) == Darwin ]]; then chmod +a \"\$(id -un) deny delete\" '$dir/sandboxes/workspace-aaa/skills'; fi; echo '# other' > '$dir/sandboxes/workspace-bbb/AGENTS.md'; [[ \$(node '$CLI' unload --clone \$c | jq .purged) == true && ! -e '$dir/sandboxes/workspace-aaa' && -e '$dir/sandboxes/workspace-bbb' ]]"
  check "$schema: --entry-only keeps the files until purge" \
    "a=\$(node '$CLI' unload --clone machine-ifrs-sec01-generalpurposefs-verifier.lsp-1.env-13 --entry-only | jq -r .agentId); [[ -d '$dir/workspaces/clones/'\$a && -d '$dir/agents/'\$a ]] && ! grep -q \"\$a\" '$dir/openclaw.json' && node '$CLI' purge --clone machine-ifrs-sec01-generalpurposefs-verifier.lsp-1.env-13 >/dev/null && [[ ! -e '$dir/workspaces/clones/'\$a && ! -e '$dir/agents/'\$a ]]"
  check "$schema: the config is the adopted baseline" "cmp -s '$dir/openclaw.json' '$dir/openclaw.json.bak' && cmp -s '$dir/openclaw.json' '$dir/openclaw.json.last-good'"
  check "$schema: the config keeps its schema" \
    "jq -e 'if \"$schema\" == \"entries\" then (.agents | has(\"entries\") and (has(\"list\") | not)) else (.agents | has(\"list\") and (has(\"entries\") | not)) end' '$dir/openclaw.json' >/dev/null"
  check "$schema: no lock is left behind" "[[ ! -e '$dir/openclaw.json.clone-agents.lock' ]]"
  check "$schema: an unknown template is refused" "! node '$CLI' load --template machine-nope --clone x 2>/dev/null"
}

run_schema entries
run_schema list
exit "$FAILED"
