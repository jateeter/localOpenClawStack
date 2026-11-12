# OpenClaw is kept current, never pinned (owner, 2026-10-29; RealityEngine_CI#663).
#
# The floor stays as a safety check: a host below it is updated, or refused if
# the update fails. The target is npm's current `openclaw@latest`. When that
# lookup fails the installed version is kept, and the caller says so.
#
#   openclaw_min                    the floor (OPENCLAW_MIN, default below)
#   openclaw_current <npm>          npm's latest, or nothing when it can't be read
#   openclaw_installed <bin> [PATH] the version <bin> reports, or nothing
#   oc_version_lt <a> <b>           a < b, comparing dotted versions numerically

# RealityEngine_CI release/host-requirements.json openclaw.min.
OPENCLAW_MIN_DEFAULT="2026.10.5"

openclaw_min() { printf '%s' "${OPENCLAW_MIN:-$OPENCLAW_MIN_DEFAULT}"; }

openclaw_current() {
  local npm_bin="${1:-npm}" v
  # A prerelease is never "current": latest is the stable tag.
  v="$("$npm_bin" view openclaw dist-tags.latest --fetch-timeout=10000 2>/dev/null | tr -d '[:space:]')" || return 0
  [[ "$v" =~ ^[0-9]+(\.[0-9]+)+$ ]] && printf '%s' "$v"
  return 0
}

openclaw_installed() {
  local bin="$1" path="${2:-$PATH}"
  [[ -n "$bin" && -x "$bin" ]] || return 0
  env PATH="$path" "$bin" --version 2>/dev/null | awk '{print $2}'
}

oc_version_lt() {
  [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" == "$1" ]]
}
