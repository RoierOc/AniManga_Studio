#!/usr/bin/env bash
# Detiene TODOS los servicios de Manga Upscaler de forma fiable.
#
# El bug anterior: start_server.sh corre un WATCHDOG (`while true`) que respawnea
# Flask cada 5s. Si solo matabas el Python (o buscabas "app.run(port=5101", patrón
# que ni coincide porque el server usa waitress.serve), el watchdog lo revivía →
# "no detiene nada". La cura: matar PRIMERO el watchdog, luego el Python.
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
stopped=0

# 1) Watchdog (start_server.sh) — PRIMERO, o respawnea Flask cada 5s.
if pkill -f 'start_server\.sh' 2>/dev/null; then
  echo -e "${GREEN}✓  Watchdog (start_server.sh) detenido${RESET}"; stopped=1
fi
sleep 1

# 2) Servidor Flask/waitress (el proceso python -c "from app import app … serve(app …)").
#    El patrón vive en este archivo, no en tu shell, así que pkill no se mata a sí mismo.
if pkill -f 'from app import app' 2>/dev/null; then
  echo -e "${GREEN}✓  Flask/waitress detenido${RESET}"; stopped=1
fi

# 3) Suwayomi (kill robusto: PID file + patrón del JAR + puerto + Xvfb).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if bash "$SCRIPT_DIR/suwayomi/stop.sh" 2>/dev/null | grep -q "stopped"; then
  echo -e "${GREEN}✓  Suwayomi detenido${RESET}"; stopped=1
fi

# 3b) Servarr: si la app los encendió, la app los apaga (si no, quedan tres .NET zombis
#     comiendo RAM tras cerrar — el mismo fallo que ya arreglamos con Suwayomi).
if bash "$SCRIPT_DIR/servarr/stop.sh" 2>/dev/null | grep -q " parado"; then
  echo -e "${GREEN}✓  Servarr detenido${RESET}"; stopped=1
fi

# 4) MPV (Windows) que el backend lanzó para el anime — se registran sus PIDs de
#    Windows en este archivo; los cerramos con taskkill.exe (interop WSL→Windows).
#    Solo mata los mpv.exe que ESTA app abrió, nunca un mpv ajeno del usuario.
if [ -f /tmp/manga_mpv_pids ] && command -v taskkill.exe >/dev/null 2>&1; then
  while read -r _mpv_pid; do
    [ -n "$_mpv_pid" ] && taskkill.exe /PID "$_mpv_pid" /F >/dev/null 2>&1 && stopped=1
  done < /tmp/manga_mpv_pids
  rm -f /tmp/manga_mpv_pids 2>/dev/null
fi

# 5) Lock huérfano (lo toma start_server.sh con flock; al morir se libera, pero por si acaso).
rm -f /tmp/manga_server.lock 2>/dev/null

if [ $stopped -eq 0 ]; then
  echo -e "${YELLOW}No hay servicios corriendo${RESET}"
fi
exit 0
