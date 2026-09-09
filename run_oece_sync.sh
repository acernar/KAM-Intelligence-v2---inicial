#!/bin/zsh
# Lanzador local para ejecución manual o mediante launchd.
set -euo pipefail
export PYTHONUNBUFFERED=1

SCRIPT_DIR="${0:A:h}"
exec "$SCRIPT_DIR/.venv/bin/python" -u "$SCRIPT_DIR/seace_sync.py" \
  --dias 1 \
  --max-paginas 3 \
  --sin-alertas-calendario
