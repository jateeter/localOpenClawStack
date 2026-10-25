#!/usr/bin/env bash
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${CYAN}[stop]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC}   $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ── Flags ─────────────────────────────────────────────────────────────────────
VOLUMES=false
for arg in "$@"; do
  case "$arg" in
    --volumes) VOLUMES=true ;;
    --native) ;;  # accepted for symmetry with start.sh; a loaded native gateway is always stopped
    --help|-h)
      echo "Usage: $0 [--volumes] [--native]"
      echo "  --volumes   also delete openclaw/, openwebui-data/, and browser-config/"
      exit 0 ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

# ── Native gateway ────────────────────────────────────────────────────────────
# Before the containers: it attaches to the browser sidecar (RealityEngine_CI#592).
if launchctl print "gui/$(id -u)/${OPENCLAW_NATIVE_LABEL:-org.realityengine.openclaw-gateway}" >/dev/null 2>&1; then
  info "Stopping the native gateway..."
  "$ROOT_DIR/scripts/native-gateway.sh" down
fi

# ── Docker services ───────────────────────────────────────────────────────────
info "Stopping Docker services..."
docker compose down
ok "Containers stopped"

# ── Optional data wipe ────────────────────────────────────────────────────────
if [[ "$VOLUMES" == true ]]; then
  warn "--volumes: removing openclaw/, openwebui-data/, browser-config/"
  rm -rf openclaw openwebui-data browser-config
  ok "Data directories removed"
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}localOpenClawStack stopped.${NC}"
if [[ "$VOLUMES" == false ]]; then
  echo "  Session data persists in openclaw/, openwebui-data/, browser-config/"
  echo "  To wipe data on next stop:  ./scripts/stop.sh --volumes"
fi
echo "  To restart:  ./scripts/start.sh"
echo ""
