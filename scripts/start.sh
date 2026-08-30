#!/usr/bin/env bash
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${CYAN}[start]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC}   $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }
die()  { echo -e "${RED}[error]${NC} $*"; exit 1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ── Flags ─────────────────────────────────────────────────────────────────────
FRESH=false
UPDATE=false
PURGE_CACHES=false
AGENT_PROFILE="${OPENCLAW_AGENT_PROFILE:-full}"
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=true ;;
    --purge-caches) PURGE_CACHES=true ;;
    --update) UPDATE=true ;;
    --agent-profile=*) AGENT_PROFILE="${arg#*=}" ;;
    --help|-h)
      echo "Usage: $0 [--fresh] [--purge-caches] [--update] [--agent-profile=NAME]"
      echo "  --fresh           reset application state (openclaw/, browser-config/) before starting"
      echo "  --purge-caches    ALSO drop package caches and volumes — forces Open WebUI to"
      echo "                    re-download its embedding model (~5 min). Rarely wanted."
      echo "  --update          resolve current stable releases and refresh immutable image pins"
      echo "  --agent-profile   which machine-behavior agents to load (default: full)"
      echo "                      full        the whole corpus (1320 agents)"
      echo "                      regression  only the agents bound to the RealityEngine"
      echo "                                  regression machine corpus (12 agents)"
      echo "                    Any name in machine-behaviors/agents/profiles/ is valid."
      exit 0 ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

# ── .env ──────────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  warn ".env not found — copying from .env.example"
  cp .env.example .env
  die "Edit .env and set OPENCLAW_GATEWAY_TOKEN, then re-run."
fi
# shellcheck source=/dev/null
set -a; source .env; set +a

if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" || "${OPENCLAW_GATEWAY_TOKEN}" == "change-me-to-a-random-secret" ]]; then
  die "OPENCLAW_GATEWAY_TOKEN is not set in .env — edit .env before starting."
fi

for REQUIRED_VAR in WEBUI_ADMIN_EMAIL WEBUI_ADMIN_PASSWORD WEBUI_SECRET_KEY; do
  VALUE="${!REQUIRED_VAR:-}"
  if [[ -z "$VALUE" || "$VALUE" == change-me-* || "$VALUE" == "admin@example.com" ]]; then
    die "$REQUIRED_VAR is not securely configured. Run ./scripts/init-secrets.sh."
  fi
done

# ── Optional fresh wipe ───────────────────────────────────────────────────────
#
# Resetting application state and destroying package caches are two different
# operations, and conflating them made every regression run pay a cold-download
# tax (RealityEngine_CI#206).
#
# `openwebui-data` holds Open WebUI's first-boot embedding-model cache
# (sentence-transformers/all-MiniLM-L6-v2 under
# /app/backend/data/cache/embedding). Removing it makes the container re-fetch
# ~30 files before it will answer /health — about five minutes, which is longer
# than the readiness gate below used to allow, so a "fresh" run reliably
# aborted startUniverse before the live tests ran. That cache is a property of
# the pinned image, not of the previous run, and nothing a regression is
# testing depends on it being cold.
#
# So --fresh now resets what a run actually dirties: the openclaw runtime state
# and the browser profile. Volumes and the model cache survive, and a re-run on
# an unchanged pin set starts fast.
#
# --purge-caches is the old behaviour, kept for when the caches are genuinely
# suspect. `--update` changes the pins and is the ordinary way to move versions.
if [[ "$FRESH" == true ]]; then
  warn "--fresh: resetting application state (openclaw/, browser-config/)"
  docker compose down 2>/dev/null || true
  if [[ -d openclaw ]]; then
    find openclaw -mindepth 1 -maxdepth 1 ! -name claude.md -exec rm -rf {} +
  fi
  rm -rf browser-config
  ok "Application state cleared (package caches preserved)"
fi

if [[ "$PURGE_CACHES" == true ]]; then
  warn "--purge-caches: dropping volumes and openwebui-data/"
  warn "  Open WebUI will re-download its embedding model on next boot (~5 min)."
  docker compose down --volumes 2>/dev/null || true
  rm -rf openwebui-data
  ok "Package caches cleared"
fi

if [[ "$UPDATE" == true ]]; then
  "$ROOT_DIR/scripts/update-versions.sh"
  # shellcheck source=/dev/null
  set -a; source .env; set +a
fi

for IMAGE_VAR in NODE_IMAGE OPEN_WEBUI_IMAGE BROWSER_IMAGE; do
  IMAGE_REF="${!IMAGE_VAR:-}"
  [[ "$IMAGE_REF" == *@sha256:* ]] || \
    die "$IMAGE_VAR is not digest-pinned. Run ./scripts/update-versions.sh or start with --update."
done

# Resolve the agent profile once and hand the same index to the sync, the
# config verifier, and the live count gate below. Three consumers deriving the
# expected agent set independently is how they end up disagreeing.
OPENCLAW_AGENT_INDEX_PATH="$("$ROOT_DIR/scripts/agent-profile.sh" "$AGENT_PROFILE")"
export OPENCLAW_AGENT_PROFILE="$AGENT_PROFILE"
export OPENCLAW_AGENT_INDEX_PATH
info "Agent profile: $AGENT_PROFILE ($(jq -r '.total' "$OPENCLAW_AGENT_INDEX_PATH") machine agents)"

"$ROOT_DIR/scripts/harden-config.sh"
"$ROOT_DIR/scripts/sync-machine-agents.sh"
"$ROOT_DIR/scripts/verify-openclaw-config.sh"
chmod 700 "$ROOT_DIR/openclaw" "$ROOT_DIR/openwebui-data" "$ROOT_DIR/browser-config" 2>/dev/null || true
EXPECTED_AGENT_COUNT="$(( $(jq -r '.total' "$OPENCLAW_AGENT_INDEX_PATH") + 1 ))"

# ── Port pre-flight ───────────────────────────────────────────────────────────
GW_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
UI_PORT="${OPEN_WEBUI_PORT:-8080}"
EXISTING_GATEWAY_CID="$(docker compose ps -q openclaw-gateway 2>/dev/null || true)"

if [[ -z "$(docker compose ps -q 2>/dev/null)" ]]; then
  for PORT in "$GW_PORT" "$UI_PORT"; do
    if lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
      die "Port $PORT is already in use. Stop the conflicting process or change the port in .env."
    fi
  done
  ok "Ports $GW_PORT and $UI_PORT are free"
else
  info "Existing compose services detected; preserving their port ownership during upgrade"
fi

# ── Docker services ───────────────────────────────────────────────────────────
info "Starting Docker services (browser, openclaw-gateway, open-webui)..."
# Pull only what is not already on disk. The images are digest-pinned, so a pin
# that is present locally is byte-identical to the one upstream — re-pulling it
# fetches multi-GB layers to arrive at the same content, churns the Docker VM
# disk, and contributed to the out-of-disk failures during regression runs
# (RealityEngine_CI#206).
#
# `--update` is what moves a pin; Dependabot is what proposes the move. Absent
# either, a run has nothing to fetch.
for IMAGE_VAR in OPEN_WEBUI_IMAGE BROWSER_IMAGE; do
  IMAGE_REF="${!IMAGE_VAR:-}"
  if [[ -z "$IMAGE_REF" ]]; then
    warn "$IMAGE_VAR is unset — skipping"
    continue
  fi
  if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
    info "$IMAGE_VAR already present at its pinned digest — not pulling"
  else
    info "Pulling $IMAGE_VAR ($IMAGE_REF)"
    docker pull --quiet "$IMAGE_REF"
  fi
done
# --pull only when the base moved: the gateway image is built here, and forcing
# a base re-pull on every run is the same tax in a smaller package.
docker compose build openclaw-gateway
docker compose up -d --remove-orphans
if [[ -n "$EXISTING_GATEWAY_CID" ]]; then
  info "Restarting openclaw-gateway to reload synced machine agents..."
  docker compose restart openclaw-gateway >/dev/null
fi
ok "Containers started"

# ── Wait for openclaw-gateway ─────────────────────────────────────────────────
info "Waiting for openclaw-gateway (port $GW_PORT)..."
for i in $(seq 1 30); do
  if curl -sf "http://localhost:${GW_PORT}/healthz" >/dev/null 2>&1; then
    ok "openclaw-gateway ready"
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    die "openclaw-gateway not ready after 60s; check docker compose logs openclaw-gateway"
  fi
done

LIVE_AGENT_COUNT="$(docker compose exec -T openclaw-gateway sh -lc 'node -e '"'"'
const fs = require("fs");
const cfg = JSON.parse(fs.readFileSync("/home/node/.openclaw/openclaw.json", "utf8"));
console.log((cfg.agents?.list || []).length);
'"'"'' | tr -d '\r')"
# Exact, not "at least": a wider count is the signature of the gateway restoring
# an older, wider config over the profile-narrowed one, and `-lt` waved that
# through here only for verify-deployment.sh to fail on the same file minutes
# later with no hint about when it changed.
if [[ "$LIVE_AGENT_COUNT" != "$EXPECTED_AGENT_COUNT" ]]; then
  CLOBBERED="$(ls -t "$ROOT_DIR"/openclaw/openclaw.json.clobbered.* 2>/dev/null | head -n 1 || true)"
  if [[ -n "$CLOBBERED" ]]; then
    warn "OpenClaw quarantined the synced config as $(basename "$CLOBBERED") and restored a backup"
    warn "Its clobber guard reads openclaw/logs/config-health.json and openclaw.json.bak as the baseline; scripts/adopt-config-baseline.sh keeps them in step with the active profile"
  fi
  die "OpenClaw loaded $LIVE_AGENT_COUNT agents, expected exactly $EXPECTED_AGENT_COUNT for profile '$AGENT_PROFILE'"
fi
ok "OpenClaw machine agents loaded ($LIVE_AGENT_COUNT total)"

# ── Wait for open-webui ───────────────────────────────────────────────────────
info "Waiting for open-webui (port $UI_PORT)..."
# Configurable, and defaulted high enough for a genuine first-boot population.
#
# This was a hard-coded 180s. On a cache-less boot Open WebUI downloads its
# embedding model — ~30 files, about five minutes — before it answers /health,
# so the gate expired mid-download and `die`d, aborting startUniverse before
# the regression's live phases ran. It ignored --warn-only, so a cold run could
# not proceed at all (RealityEngine_CI#206).
#
# Preserving the cache above means this rarely matters now; the default covers
# the case where it legitimately does — the first boot after a pin change.
OPENWEBUI_READY_TIMEOUT="${OPENWEBUI_READY_TIMEOUT:-600}"
OPENWEBUI_READY_ATTEMPTS=$(( OPENWEBUI_READY_TIMEOUT / 3 ))
for i in $(seq 1 "$OPENWEBUI_READY_ATTEMPTS"); do
  HTTP_STATUS=$(curl -so /dev/null -w "%{http_code}" "http://localhost:${UI_PORT}/health" 2>/dev/null || true)
  if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "open-webui ready"
    break
  fi
  sleep 3
  if [[ $i -eq "$OPENWEBUI_READY_ATTEMPTS" ]]; then
    die "open-webui not ready after ${OPENWEBUI_READY_TIMEOUT}s; check docker compose logs open-webui (raise OPENWEBUI_READY_TIMEOUT for a first-boot model download)"
  fi
done

# ── Sync WebUI admin credentials ─────────────────────────────────────────────
"$ROOT_DIR/scripts/sync-webui-admin.sh" || \
  warn "sync-webui-admin did not complete — run ./scripts/sync-webui-admin.sh manually if login fails"

docker compose exec -T openclaw-gateway chmod 700 /home/node/.openclaw

echo ""
echo -e "${GREEN}localOpenClawStack running.${NC}"
echo "  OpenClaw gateway  →  http://localhost:${GW_PORT}"
echo "  Open WebUI        →  http://localhost:${UI_PORT}"
echo ""
echo "  API key (OPENAI-compatible):  OPENCLAW_GATEWAY_TOKEN from .env"
echo "  WebUI account:                 WEBUI_ADMIN_EMAIL from .env"
echo "  Logs:  docker compose logs -f"
echo "  Stop:  ./scripts/stop.sh"
echo ""

"$ROOT_DIR/scripts/verify-deployment.sh"
