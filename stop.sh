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

# 3) Suwayomi (por PID file).
if [ -f /tmp/suwayomi.pid ] && kill -0 "$(cat /tmp/suwayomi.pid)" 2>/dev/null; then
  kill "$(cat /tmp/suwayomi.pid)" && echo -e "${GREEN}✓  Suwayomi detenido${RESET}"
  rm -f /tmp/suwayomi.pid; stopped=1
fi

# 4) Lock huérfano (lo toma start_server.sh con flock; al morir se libera, pero por si acaso).
rm -f /tmp/manga_server.lock 2>/dev/null

if [ $stopped -eq 0 ]; then
  echo -e "${YELLOW}No hay servicios corriendo${RESET}"
fi
exit 0
