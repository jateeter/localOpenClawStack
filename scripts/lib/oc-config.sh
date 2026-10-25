# Shared by the scripts that read or write openclaw.json. Source it; it sets
# variables and defines jq text, and runs nothing.
#
# Two OpenClaw config schemas are in use (RealityEngine_CI#592):
#
#   list     OpenClaw <= 2026.6.x: agents.list is an array of {id, ...},
#            gateway.nodes.denyCommands. Read only to migrate it: no gateway this
#            stack pins writes it any more (scripts/migrate-state.sh).
#   entries  OpenClaw >= 2026.9, the container image and the native host install:
#            agents.entries is an object keyed by agent id (a key is
#            [a-z0-9_][a-z0-9_-]{0,63}), gateway.nodes.commands.deny,
#            agents.ownership "explicit", and a model allow-list under
#            agents.defaults.modelPolicy.allow.
#
# A config that exists decides for itself (agents.entries or agents.list); a fresh
# one takes OPENCLAW_SCHEMA, default `entries`.
#
# Where things are:
#   OC_DIR     the state directory on this machine   (OPENCLAW_STATE_DIR, else <repo>/openclaw)
#   OC_CONFIG  the config file                       (OPENCLAW_CONFIG_PATH, else $OC_DIR/openclaw.json)
#   OC_HOME    the same directory as the *gateway* sees it, which is what goes into the
#              config's workspace and agentDir paths (OPENCLAW_HOME_DIR, default
#              /home/node/.openclaw for the container; the native gateway's is $OC_DIR).

OC_DIR="${OPENCLAW_STATE_DIR:-$ROOT_DIR/openclaw}"
OC_CONFIG="${OPENCLAW_CONFIG_PATH:-$OC_DIR/openclaw.json}"
OC_HOME="${OPENCLAW_HOME_DIR:-/home/node/.openclaw}"

# oc_schema [config]: list | entries
oc_schema() {
  local cfg="${1:-$OC_CONFIG}"
  if [[ -f "$cfg" ]]; then
    if jq -e '(.agents // {}) | has("entries")' "$cfg" >/dev/null 2>&1; then echo entries; return; fi
    if jq -e '(.agents // {}) | has("list")' "$cfg" >/dev/null 2>&1; then echo list; return; fi
  fi
  case "${OPENCLAW_SCHEMA:-entries}" in list) echo list ;; *) echo entries ;; esac
}

# jq definitions: `oc_agents` is the agents as an array of {id, ...} in either
# schema; `oc_with_agents($arr)` stores such an array back in the schema the file
# is in, deleting the other schema's key.
# shellcheck disable=SC2016  # jq text, not shell
OC_JQ_DEFS='
def oc_agents:
  if ((.agents // {}) | has("entries"))
  then ((.agents.entries // {}) | to_entries | map(.value + {id: .key}))
  else (.agents.list // []) end;
def oc_with_agents($arr; $schema):
  if $schema == "entries"
  then .agents.entries = ($arr | map({key: .id, value: del(.id, .default)}) | from_entries) | del(.agents.list)
  else .agents.list = $arr | del(.agents.entries) end;
'
