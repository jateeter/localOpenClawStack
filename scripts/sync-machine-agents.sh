#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

INDEX_PATH="$ROOT_DIR/machine-behaviors/agents/INDEX.json"
CONFIG_PATH="$ROOT_DIR/openclaw/openclaw.json"
WORKSPACE_ROOT="$ROOT_DIR/openclaw/workspaces/machine-behaviors"
AGENT_ROOT="$ROOT_DIR/openclaw/agents"
MAIN_WORKSPACE="$ROOT_DIR/openclaw/workspace"
CONTAINER_MAIN_WORKSPACE="/home/node/.openclaw/workspace"
CONTAINER_WORKSPACE_ROOT="/home/node/.openclaw/workspaces/machine-behaviors"
CONTAINER_AGENT_ROOT="/home/node/.openclaw/agents"

[[ -f "$INDEX_PATH" ]] || { echo "[machine-agents] missing $INDEX_PATH" >&2; exit 1; }
[[ -f "$CONFIG_PATH" ]] || { echo "[machine-agents] missing $CONFIG_PATH" >&2; exit 1; }
command -v jq >/dev/null || { echo "[machine-agents] jq is required" >&2; exit 1; }

DEFAULT_MODEL="$(jq -er '.agents.defaults.model.primary // "ollama/llama3.1:8b"' "$CONFIG_PATH")"
CONFIG_TMP="$(mktemp)"

jq --slurpfile idx "$INDEX_PATH" \
  --arg defaultModel "$DEFAULT_MODEL" \
  --arg mainWorkspace "$CONTAINER_MAIN_WORKSPACE" \
  --arg containerAgentRoot "$CONTAINER_AGENT_ROOT" '
  ($idx[0].agents | map(.agentId)) as $managedIds |
  .agents = (.agents // {}) |
  .agents.defaults = (.agents.defaults // {}) |
  .agents.defaults.workspace = $mainWorkspace |
  .agents.defaults.model = (.agents.defaults.model // {primary: $defaultModel}) |
  .agents.defaults.models = (.agents.defaults.models // {}) |
  .agents.defaults.models[$defaultModel] = (.agents.defaults.models[$defaultModel] // {}) |
  (.agents.list // []) as $existing |
  ($existing | map(select(((.id // "") as $id | ($managedIds | index($id)) | not)))) as $preserved |
  (
    if any($preserved[]?; .id == "main") then
      $preserved | map(if .id == "main" then . + {
        default: true,
        workspace: $mainWorkspace,
        agentDir: ($containerAgentRoot + "/main/agent"),
        model: (.model // {primary: $defaultModel})
      } else . end)
    else
      [{
        id: "main",
        name: "Main",
        default: true,
        workspace: $mainWorkspace,
        agentDir: ($containerAgentRoot + "/main/agent"),
        model: {primary: $defaultModel}
      }] + $preserved
    end
  ) as $preservedWithMain |
  ($idx[0].agents | map({
    id: .agentId,
    name: .machineName,
    workspace: ("/home/node/.openclaw/workspaces/machine-behaviors/" + .agentId),
    agentDir: ("/home/node/.openclaw/agents/" + .agentId + "/agent"),
    model: {primary: $defaultModel},
    identity: {
      name: .machineName,
      theme: (.domain + " input analyst")
    },
    contextInjection: "always",
    bootstrapMaxChars: 50000,
    bootstrapTotalMaxChars: 120000,
    experimental: {
      localModelLean: true
    }
  })) as $machineAgents |
  .agents.list = ($preservedWithMain + $machineAgents)
' "$CONFIG_PATH" > "$CONFIG_TMP"

jq . "$CONFIG_TMP" >/dev/null
mv "$CONFIG_TMP" "$CONFIG_PATH"

mkdir -p "$MAIN_WORKSPACE" "$WORKSPACE_ROOT" "$AGENT_ROOT/main/agent"
cp "$INDEX_PATH" "$WORKSPACE_ROOT/INDEX.json"
{
  printf '# OpenClaw Machine Behaviors\n\n'
  printf 'This deployment workspace is generated from `machine-behaviors/agents/INDEX.json`.\n\n'
  printf 'It loads `%s` machine-behavior agents under `/home/node/.openclaw/workspaces/machine-behaviors`. Each agent subdirectory contains an `oc-agent.json` binding contract and an `AGENTS.md` bootstrap prompt.\n\n' "$(jq -r '.total' "$INDEX_PATH")"
  printf 'Use `openclaw agents list` to enumerate the loaded agents, or select a specific agent id when dispatching through the gateway.\n'
} > "$MAIN_WORKSPACE/AGENTS.md"
cp "$INDEX_PATH" "$MAIN_WORKSPACE/INDEX.json"
jq -n \
  --arg model "$DEFAULT_MODEL" \
  '{providers: {}, selected: {model: $model}}' > "$AGENT_ROOT/main/agent/models.json"

while IFS=$'\t' read -r agent_id machine_name domain rel_path; do
  [[ -n "$agent_id" ]] || continue
  spec_path="$ROOT_DIR/machine-behaviors/agents/$rel_path"
  workspace_dir="$WORKSPACE_ROOT/$agent_id"
  agent_dir="$AGENT_ROOT/$agent_id/agent"

  mkdir -p "$workspace_dir" "$agent_dir"
  cp "$spec_path" "$workspace_dir/oc-agent.json"

  tmp_bootstrap="$(mktemp)"
  {
    printf '# %s\n\n' "$machine_name"
    printf 'Domain: `%s`\n\n' "$domain"
    printf 'You are the OpenClaw machine-behavior input analyst for `%s`.\n\n' "$machine_name"
    printf 'Use the bundled `oc-agent.json` in this workspace as your binding contract. '
    printf 'Return assessments in the structured response shape requested by that contract; '
    printf 'RealityEngine remains authoritative for CES evaluation and downstream transitions.\n\n'
    printf '## Machine Behavior Contract\n\n'
    jq -r '.reasoning.systemPrompt' "$spec_path"
    printf '\n\n## Response Contract\n\n'
    jq -r '.reasoning.outputContract' "$spec_path"
  } > "$tmp_bootstrap"
  mv "$tmp_bootstrap" "$workspace_dir/AGENTS.md"

  jq -n \
    --arg model "$DEFAULT_MODEL" \
    '{providers: {}, selected: {model: $model}}' > "$agent_dir/models.json"
done < <(jq -r '.agents[] | [.agentId, .machineName, .domain, .path] | @tsv' "$INDEX_PATH")

chmod 700 "$ROOT_DIR/openclaw" "$MAIN_WORKSPACE" "$ROOT_DIR/openclaw/workspaces" "$WORKSPACE_ROOT" "$AGENT_ROOT" 2>/dev/null || true
find "$MAIN_WORKSPACE" "$WORKSPACE_ROOT" "$AGENT_ROOT" -type d -exec chmod 700 {} + 2>/dev/null || true
find "$MAIN_WORKSPACE" "$WORKSPACE_ROOT" "$AGENT_ROOT" -type f -exec chmod 600 {} + 2>/dev/null || true
chmod 600 "$CONFIG_PATH"

COUNT="$(jq -r '.total' "$INDEX_PATH")"
echo "[machine-agents] synced $COUNT machine-behavior agents from machine-behaviors/agents"
