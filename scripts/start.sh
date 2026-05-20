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
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=true ;;
    --help|-h)
      echo "Usage: $0 [--fresh]"
      echo "  --fresh   wipe openclaw/, openwebui-data/, and browser-config/ before starting"
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

# ── Optional fresh wipe ───────────────────────────────────────────────────────
if [[ "$FRESH" == true ]]; then
  warn "--fresh: removing openclaw/, openwebui-data/, browser-config/"
  docker compose down --volumes 2>/dev/null || true
  rm -rf openclaw openwebui-data browser-config
  ok "Data directories cleared"
fi

# ── Port pre-flight ───────────────────────────────────────────────────────────
GW_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
UI_PORT="${OPEN_WEBUI_PORT:-8080}"

for PORT in "$GW_PORT" "$UI_PORT"; do
  if lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
    die "Port $PORT is already in use. Stop the conflicting process or change the port in .env."
  fi
done
ok "Ports $GW_PORT and $UI_PORT are free"

# ── Docker services ───────────────────────────────────────────────────────────
info "Starting Docker services (browser, openclaw-gateway, open-webui)..."
docker compose up -d
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
    warn "openclaw-gateway not ready after 60s"
    echo "  Check:  docker logs openclaw-gateway"
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
    warn "open-webui not ready after 60s"
    echo "  Check:  docker logs open-webui"
  fi
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}localOpenClawStack running.${NC}"
echo "  OpenClaw gateway  →  http://localhost:${GW_PORT}"
echo "  Open WebUI        →  http://localhost:${UI_PORT}"
echo ""
echo "  API key (OPENAI-compatible):  OPENCLAW_GATEWAY_TOKEN from .env"
echo "  Logs:  docker compose logs -f"
echo "  Stop:  ./scripts/stop.sh"
echo ""
