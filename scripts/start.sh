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
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=true ;;
    --update) UPDATE=true ;;
    --help|-h)
      echo "Usage: $0 [--fresh] [--update]"
      echo "  --fresh   wipe openclaw/, openwebui-data/, and browser-config/ before starting"
      echo "  --update  resolve current stable releases and refresh immutable image pins"
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
if [[ "$FRESH" == true ]]; then
  warn "--fresh: removing openclaw/, openwebui-data/, browser-config/"
  docker compose down --volumes 2>/dev/null || true
  if [[ -d openclaw ]]; then
    find openclaw -mindepth 1 -maxdepth 1 ! -name claude.md -exec rm -rf {} +
  fi
  rm -rf openwebui-data browser-config
  ok "Data directories cleared"
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

"$ROOT_DIR/scripts/harden-config.sh"

# ── Port pre-flight ───────────────────────────────────────────────────────────
GW_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
UI_PORT="${OPEN_WEBUI_PORT:-8080}"

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
docker compose pull --quiet browser open-webui
docker compose build --pull openclaw-gateway
docker compose up -d --remove-orphans
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

# ── Wait for open-webui ───────────────────────────────────────────────────────
info "Waiting for open-webui (port $UI_PORT)..."
for i in $(seq 1 20); do
  HTTP_STATUS=$(curl -so /dev/null -w "%{http_code}" "http://localhost:${UI_PORT}/" 2>/dev/null || true)
  if [[ "$HTTP_STATUS" =~ ^(200|302)$ ]]; then
    ok "open-webui ready"
    break
  fi
  sleep 3
  if [[ $i -eq 20 ]]; then
    die "open-webui not ready after 60s; check docker compose logs open-webui"
  fi
done

# ── Banner ────────────────────────────────────────────────────────────────────
"$ROOT_DIR/scripts/sync-webui-admin.sh"

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
