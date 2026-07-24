#!/usr/bin/env bash
# Arranca el sidecar de novelas (plugins LNReader) en 127.0.0.1:4568. Idempotente.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${NOVELS_PORT:-4568}"
if curl -s "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "[novels] ya está vivo en :${PORT}"; exit 0
fi
# Node no trae las raíces nuevas de Let's Encrypt: sin esto, varias fuentes fallan por TLS.
bash "$DIR/fetch-ca.sh" || true
CA="$DIR/sidecar/certs/extra-ca.pem"
[ -s "$CA" ] && export NODE_EXTRA_CA_CERTS="$CA"

cd "$DIR/sidecar"
[ -d node_modules ] || pnpm install
# Ruta COMPLETA a propósito: node resuelve require/__dirname/node_modules por la del módulo, no por
# el cwd, así que el cmdline lleva `novels/sidecar/server.cjs` y stop.sh lo puede encontrar. Antes
# se lanzaba `node server.cjs` (arg pelado) y el pkill de stop.sh nunca casaba → sidecar zombi.
nohup node "$DIR/sidecar/server.cjs" > "$DIR/novels.log" 2>&1 &
echo "[novels] lanzado (PID $!) — log: $DIR/novels.log"
