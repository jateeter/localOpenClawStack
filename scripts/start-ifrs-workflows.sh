#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${IFRS_PYTHON:?Set IFRS_PYTHON to the interpreter with document-workflows/requirements.txt installed}"
: "${IFRS_WORKFLOW_PRINCIPALS:?Set a private principal credential file outside Git}"
: "${IFRS_ASSETS_DIR:?Set the reviewed IFRS document asset directory}"
: "${IFRS_MACHINES_DIR:?Set the IFRS candidate machine directory}"
: "${IFRS_DOCUMENT_STATE_DIR:?Set persistent canonical document storage outside Git}"
: "${IFRS_RAG_URL:?Set the scoped localAI service URL}"
: "${IFRS_EVIDENCE_TOKEN:?Set the localAI evidence service credential}"
cd "$root/document-workflows"
exec "$IFRS_PYTHON" -m uvicorn service:configured_app --factory --host 127.0.0.1 --port "${IFRS_DOCUMENT_PORT:-8792}"
