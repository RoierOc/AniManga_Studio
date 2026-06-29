#!/usr/bin/env bash
# Reinicia Flask para recargar el código de disco, SIN tocar Suwayomi/qBittorrent.
#
# Modo rápido (--fast / por defecto cuando el watchdog está vivo):
#   Mata solo el proceso Python. El watchdog lo relanza en ~5s con el código nuevo.
#   Útil para cambios de código Python donde el host binding no cambia.
#
# Modo completo (--full / automático cuando cambia el host binding):
#   Mata el watchdog entero y relanza start_server.sh. Necesario cuando se modifica
#   start_server.sh (p.ej. cambiar host de 127.0.0.1 a 0.0.0.0).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FULL=0
[[ "${1:-}" == "--full" ]] && FULL=1

old="$(pgrep -f 'from app import app' | head -1)"

if [[ $FULL -eq 1 ]]; then
  echo "[restart] Modo completo → mato watchdog + Flask y relanco start_server.sh…"
  pkill -f 'from app import app' 2>/dev/null || true
  pkill -f 'start_server\.sh'   2>/dev/null || true
  sleep 2
  rm -f /tmp/manga_server.lock
  "$SCRIPT_DIR/start_server.sh"
  echo "[restart] start_server.sh relanzado en segundo plano. Esperando Flask…"
elif pgrep -f 'start_server\.sh' >/dev/null 2>&1; then
  echo "[restart] Watchdog vivo → mato Flask; lo relanzará con el código nuevo…"
  pkill -f 'from app import app' 2>/dev/null
else
  echo "[restart] Sin watchdog → arranco la pila completa…"
  rm -f /tmp/manga_server.lock
  "$SCRIPT_DIR/start_server.sh"
fi

# Sondeamos hasta ~40s a que el 5101 responda con PID distinto al anterior.
for i in $(seq 1 40); do
  sleep 1
  new="$(pgrep -f 'from app import app' | head -1)"
  if [ -n "$new" ] && [ "$new" != "$old" ] \
     && curl -sf --connect-timeout 1 http://127.0.0.1:5101/api/library >/dev/null 2>&1; then
    host="$(ss -tlnp 2>/dev/null | awk '/5101/{print $4}' | head -1)"
    echo "[restart] ✓ Flask reiniciado (PID $old → $new) — escuchando en $host"
    exit 0
  fi
done

echo "[restart] ⚠ El 5101 no respondió a tiempo; revisa: tail -n 40 /tmp/manga_server.log" >&2
exit 1
