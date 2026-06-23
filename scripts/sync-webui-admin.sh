#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=/dev/null
set -a; source .env; set +a

docker compose exec -T open-webui python - <<'PY'
import os
import sqlite3
import bcrypt

email = os.environ["WEBUI_ADMIN_EMAIL"]
password = os.environ["WEBUI_ADMIN_PASSWORD"]
db = sqlite3.connect("/app/backend/data/webui.db")
row = db.execute("SELECT id FROM user WHERE email = ? AND role = 'admin'", (email,)).fetchone()
if row is None:
    raise SystemExit(f"No persisted admin matches WEBUI_ADMIN_EMAIL={email}")
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
db.execute("UPDATE auth SET password = ?, active = 1 WHERE id = ?", (hashed, row[0]))
db.commit()
print(f"Synchronized persisted admin password for {email}.")
PY
