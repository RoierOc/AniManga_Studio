#!/usr/bin/env bash
# Reinicia SOLO Flask para recargar el código de disco, SIN tocar Suwayomi/qBittorrent
# (que tardan en arrancar). Aprovecha el watchdog de start_server.sh: si está vivo, basta
# matar el proceso Python y el watchdog lo relanza en ~5s reimportando todo desde disco.
#
# Por qué un archivo y no un pkill suelto en la consola: el patrón "from app import app"
# vive AQUÍ, no en tu línea de comandos, así que pkill no puede matar tu propia shell.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

old="$(pgrep -f 'from app import app' | head -1)"

if pgrep -f 'start_server\.sh' >/dev/null 2>&1; then
  echo "[restart] Watchdog vivo → mato Flask; lo relanzará con el código nuevo…"
  pkill -f 'from app import app' 2>/dev/null
else
  echo "[restart] Sin watchdog → arranco la pila completa…"
  rm -f /tmp/manga_server.lock
  "$SCRIPT_DIR/start_server.sh"
fi

# El watchdog espera 5s antes de respawnear; sondeamos hasta ~40s a que el 5101 responda
# con un PID DISTINTO al anterior (garantiza que es el proceso nuevo, no el viejo).
for i in $(seq 1 40); do
  sleep 1
  new="$(pgrep -f 'from app import app' | head -1)"
  if [ -n "$new" ] && [ "$new" != "$old" ] \
     && curl -sf --connect-timeout 1 http://127.0.0.1:5101/api/library >/dev/null 2>&1; then
    echo "[restart] ✓ Flask reiniciado (PID $old → $new) — http://127.0.0.1:5101"
    exit 0
  fi
done

echo "[restart] ⚠ El 5101 no respondió a tiempo; revisa: tail -n 40 /tmp/manga_server.log" >&2
exit 1
