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
    --help|-h)
      echo "Usage: $0 [--volumes]"
      echo "  --volumes   also delete openclaw/, openwebui-data/, and browser-config/"
      exit 0 ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

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
