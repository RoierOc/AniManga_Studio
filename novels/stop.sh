#!/usr/bin/env bash
# Detiene el sidecar de novelas de forma FIABLE.
#
# El patrón por ruta (`novels/sidecar/server.cjs`) casa ahora que start.sh lanza con la ruta
# completa; el respaldo por PUERTO cubre el caso de un sidecar viejo lanzado con el arg pelado
# (`node server.cjs`), que el pkill por ruta no veía y dejaba zombi. Mismo espíritu que el
# backstop por puerto de suwayomi/stop.sh.
PORT="${NOVELS_PORT:-4568}"
killed=0

if pkill -f "novels/sidecar/server.cjs" 2>/dev/null; then killed=1; fi

# Respaldo: lo que sea que escuche en el puerto del sidecar (cmdline distinto, orphan…).
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 && killed=1
fi

[ "$killed" -eq 1 ] && echo "[novels] detenido" || echo "[novels] no estaba corriendo"
exit 0
