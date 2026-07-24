#!/usr/bin/env bash
# Arranca FlareSolverr en 127.0.0.1:8191. Idempotente (como suwayomi/start.sh).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${FLARESOLVERR_PORT:-8191}"

if curl -s -m 3 "http://127.0.0.1:${PORT}/v1" -o /dev/null 2>&1; then
  echo "[flaresolverr] ya está vivo en :${PORT}"; exit 0
fi

bash "$DIR/fetch.sh"

# WSLg monta /tmp/.X11-unix como READ-ONLY, así que Xvfb no puede crear su socket y muere con
# "Xvfb display did not open". Lo reemplazamos por un tmpfs escribible (mode 1777) preservando el
# display :0 de WSLg vía symlink al X0 original. Sólo si hace falta (idempotente) y sólo como root.
if [ "$(id -u)" = 0 ] && ! ( : > /tmp/.X11-unix/.wtest 2>/dev/null && rm -f /tmp/.X11-unix/.wtest ); then
  echo "[flaresolverr] /tmp/.X11-unix es read-only (WSLg) — montando tmpfs escribible"
  mkdir -p /tmp/.x11-wslg
  mountpoint -q /tmp/.x11-wslg || mount --bind /tmp/.X11-unix /tmp/.x11-wslg
  mount -t tmpfs -o mode=1777 tmpfs /tmp/.X11-unix
  [ -e /tmp/.x11-wslg/X0 ] && ln -sf /tmp/.x11-wslg/X0 /tmp/.X11-unix/X0
fi

cd "$DIR/flaresolverr"
# Solo localhost y sin telemetría; LOG_LEVEL=warning para no llenar el log de ruido.
# Ver shim/sh: sin él, la detección de Chromium falla en Arch.
# /usr/sbin va explícito: ahí vive Xvfb en Arch y NO está en el PATH de un shell no-root.
# Sin él, FlareSolverr arranca y muere con "Xvfb display did not open" — que parece un fallo
# gráfico y en realidad es un binario que no se encuentra.
PATH="$DIR/shim:$PATH:/usr/sbin" \
  HOST=127.0.0.1 PORT="$PORT" LOG_LEVEL="${FLARESOLVERR_LOG:-warning}" \
  nohup ./flaresolverr > "$DIR/flaresolverr.log" 2>&1 &
echo "[flaresolverr] lanzado (PID $!) — log: $DIR/flaresolverr.log"
