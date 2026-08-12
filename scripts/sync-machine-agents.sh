#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Which agents this deployment loads. `full` is the whole corpus and remains the
# default; a named profile (machine-behaviors/agents/profiles/<name>.txt) narrows
# it. The regression profile exists because loading 1320 agents to exercise 12
# machines costs time and memory on every start.
AGENT_PROFILE="${OPENCLAW_AGENT_PROFILE:-full}"
INDEX_PATH="${OPENCLAW_AGENT_INDEX_PATH:-$("$ROOT_DIR/scripts/agent-profile.sh" "$AGENT_PROFILE")}"
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
  --arg containerAgentRoot "$CONTAINER_AGENT_ROOT" \
  --arg machineWorkspacePrefix "$CONTAINER_WORKSPACE_ROOT/" '
  ($idx[0].agents | map(.agentId)) as $managedIds |
  .agents = (.agents // {}) |
  .agents.defaults = (.agents.defaults // {}) |
  .agents.defaults.workspace = $mainWorkspace |
  .agents.defaults.model = (.agents.defaults.model // {primary: $defaultModel}) |
  .agents.defaults.models = (.agents.defaults.models // {}) |
  .agents.defaults.models[$defaultModel] = (.agents.defaults.models[$defaultModel] // {}) |
  (.agents.list // []) as $existing |
  # Entries this sync does not manage are kept, so a hand-added agent survives.
  # A machine-behavior agent from a *previous* profile is not kept: it is
  # identified by its generated workspace path and dropped, otherwise narrowing
  # the profile would leave the old corpus resident in the config.
  ($existing
    | map(select(((.id // "") as $id | ($managedIds | index($id)) | not)))
    | map(select(((.workspace // "") | startswith($machineWorkspacePrefix)) | not))
  ) as $preserved |
  (
    if any($preserved[]?; .id == "main") then
      $preserved | map(if .id == "main" then . + {
        default: true,
        workspace: $mainWorkspace,
        agentDir: ($containerAgentRoot + "/main/agent"),
        model: (.model // {primary: $defaultModel}),
        sandbox: ((.sandbox // {}) + {mode: "all"})
      } else . end)
    else
      [{
        id: "main",
        name: "Main",
        default: true,
        workspace: $mainWorkspace,
        agentDir: ($containerAgentRoot + "/main/agent"),
        model: {primary: $defaultModel},
        sandbox: {mode: "all"}
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
    sandbox: {
      mode: "all"
    },
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
  printf 'This deployment workspace is generated from the `%s` agent profile of `machine-behaviors/agents/INDEX.json`.\n\n' "$AGENT_PROFILE"
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

# Prune agents left behind by a previous, wider profile. Without this, narrowing
# the profile shrinks openclaw.json but leaves the old corpus on disk — 32 MB of
# workspaces the gateway may still enumerate, and no reduction in the footprint
# the profile exists to reduce. Scoped to generated directories: `main` is never
# a machine agent, and anything still in the active index is kept.
MANAGED_IDS="$(mktemp)"
trap 'rm -f "$MANAGED_IDS"' EXIT
jq -r '.agents[].agentId' "$INDEX_PATH" | sort > "$MANAGED_IDS"

prune_unmanaged() {
  local root="$1" dir name pruned=0
  [[ -d "$root" ]] || { printf '0'; return 0; }
  while IFS= read -r dir; do
    name="$(basename "$dir")"
    [[ "$name" == "main" ]] && continue
    grep -qxF "$name" "$MANAGED_IDS" && continue
    rm -rf "$dir"
    pruned=$((pruned + 1))
  done < <(find "$root" -mindepth 1 -maxdepth 1 -type d)
  printf '%s' "$pruned"
}

PRUNED_WORKSPACES="$(prune_unmanaged "$WORKSPACE_ROOT")"
PRUNED_AGENTS="$(prune_unmanaged "$AGENT_ROOT")"
if [[ "$PRUNED_WORKSPACES" != "0" || "$PRUNED_AGENTS" != "0" ]]; then
  echo "[machine-agents] pruned $PRUNED_WORKSPACES workspace(s) and $PRUNED_AGENTS agent dir(s) outside the '$AGENT_PROFILE' profile"
fi

chmod 700 "$ROOT_DIR/openclaw" "$MAIN_WORKSPACE" "$ROOT_DIR/openclaw/workspaces" "$WORKSPACE_ROOT" "$AGENT_ROOT" 2>/dev/null || true
find "$MAIN_WORKSPACE" "$WORKSPACE_ROOT" "$AGENT_ROOT" -type d -exec chmod 700 {} + 2>/dev/null || true
find "$MAIN_WORKSPACE" "$WORKSPACE_ROOT" "$AGENT_ROOT" -type f -exec chmod 600 {} + 2>/dev/null || true
chmod 600 "$CONFIG_PATH"

COUNT="$(jq -r '.total' "$INDEX_PATH")"
echo "[machine-agents] synced $COUNT machine-behavior agents from machine-behaviors/agents (profile: $AGENT_PROFILE)"
