#!/usr/bin/env bash
# Arranca el sidecar de novelas (plugins LNReader) en 127.0.0.1:4568. Idempotente.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${NOVELS_PORT:-4568}"
if curl -s "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "[novels] ya está vivo en :${PORT}"; exit 0
fi
cd "$DIR/sidecar"
[ -d node_modules ] || pnpm install
nohup node server.cjs > "$DIR/novels.log" 2>&1 &
echo "[novels] lanzado (PID $!) — log: $DIR/novels.log"
