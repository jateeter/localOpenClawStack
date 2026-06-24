#!/usr/bin/env bash
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${CYAN}[sync-webui-admin]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC}   $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=/dev/null
set -a; source .env; set +a

# Retry loop: fresh open-webui takes a few seconds to write its initial DB.
MAX_ATTEMPTS=6
DELAY=5
attempt=0
while true; do
  attempt=$((attempt + 1))
  result=$(docker compose exec -T open-webui python - <<'PY' 2>&1
import os
import sqlite3
import bcrypt

email    = os.environ["WEBUI_ADMIN_EMAIL"]
password = os.environ["WEBUI_ADMIN_PASSWORD"]

db_path = "/app/backend/data/webui.db"
import os as _os
if not _os.path.exists(db_path):
    raise SystemExit("DB_NOT_READY")

db = sqlite3.connect(db_path)

# Confirm open-webui has finished schema init (user table must exist and have our admin)
try:
    row = db.execute("SELECT id FROM user WHERE email = ? AND role = 'admin'", (email,)).fetchone()
except sqlite3.OperationalError:
    raise SystemExit("DB_NOT_READY")

if row is None:
    raise SystemExit(f"No persisted admin matches WEBUI_ADMIN_EMAIL={email}")

hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
db.execute("UPDATE auth SET password = ?, active = 1 WHERE id = ?", (hashed, row[0]))
db.commit()
print(f"OK:{email}")
PY
  )
  if echo "$result" | grep -q "^OK:"; then
    email_out=$(echo "$result" | grep "^OK:" | cut -d: -f2-)
    ok "Admin account synchronized for ${email_out}."
    break
  elif echo "$result" | grep -q "DB_NOT_READY"; then
    if [[ $attempt -ge $MAX_ATTEMPTS ]]; then
      warn "open-webui DB not ready after $((MAX_ATTEMPTS * DELAY))s — sync skipped. Re-run ./scripts/sync-webui-admin.sh when the container is healthy."
      exit 0
    fi
    info "Waiting for open-webui DB to initialize (attempt $attempt/$MAX_ATTEMPTS)..."
    sleep "$DELAY"
  else
    warn "sync-webui-admin failed: $result"
    exit 1
  fi
done
