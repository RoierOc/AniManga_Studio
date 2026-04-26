#!/usr/bin/env bash
# Stop all MangaJaNai services
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'

stopped=0

# Suwayomi
if [ -f "/tmp/suwayomi.pid" ] && kill -0 "$(cat /tmp/suwayomi.pid)" 2>/dev/null; then
  kill "$(cat /tmp/suwayomi.pid)" && echo -e "${GREEN}✓  Suwayomi detenido${RESET}"
  rm -f /tmp/suwayomi.pid
  stopped=1
fi

# Flask (por PID file o por nombre de proceso)
if [ -f "/tmp/mangajanai-flask.pid" ] && kill -0 "$(cat /tmp/mangajanai-flask.pid)" 2>/dev/null; then
  kill "$(cat /tmp/mangajanai-flask.pid)" && echo -e "${GREEN}✓  Flask detenido${RESET}"
  rm -f /tmp/mangajanai-flask.pid
  stopped=1
fi

# Fallback: kill by process name if PID file is stale
if pkill -f "app.run(port=5001" 2>/dev/null; then
  echo -e "${GREEN}✓  Flask detenido (fallback)${RESET}"
  stopped=1
fi

if [ $stopped -eq 0 ]; then
  echo -e "${YELLOW}No hay servicios corriendo${RESET}"
fi
