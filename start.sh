#!/usr/bin/env bash
# Start everything: Suwayomi + Flask
# Usage:
#   ./start.sh           — foreground (Ctrl+C para parar)
#   ./start.sh --daemon  — background, persiste al cerrar terminal

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUWAYOMI_JAR="/Manga_Upscaler_project/suwayomi/Suwayomi-Server.jar"
SUWAYOMI_LOG="/tmp/suwayomi.log"
SUWAYOMI_PID="/tmp/suwayomi.pid"
FLASK_PID="/tmp/mangajanai-flask.pid"
FLASK_LOG="/tmp/mangajanai-flask.log"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'

DAEMON=false
[[ "${1:-}" == "--daemon" ]] && DAEMON=true

# ── Re-launch as daemon ───────────────────────────────────────────────────────
if $DAEMON; then
  # If already running, just print status
  if [ -f "$FLASK_PID" ] && kill -0 "$(cat "$FLASK_PID")" 2>/dev/null; then
    echo -e "${GREEN}  ✓  MangaJaNai ya está corriendo (PID $(cat "$FLASK_PID"))${RESET}"
    echo -e "  ${CYAN}→  http://localhost:5001${RESET}"
    exit 0
  fi
  echo -e "${BOLD}${CYAN}  Iniciando MangaJaNai en background...${RESET}"
  # Use setsid + nohup to fully detach from terminal
  nohup setsid bash "$0" > "$FLASK_LOG" 2>&1 &
  BGPID=$!
  sleep 2
  if kill -0 $BGPID 2>/dev/null; then
    echo -e "${GREEN}  ✓  Corriendo en background (log: ${FLASK_LOG})${RESET}"
    echo -e "  ${CYAN}→  http://localhost:5001${RESET}"
    echo -e "  Usa ${YELLOW}./stop.sh${RESET} para parar"
  else
    echo -e "${RED}  ✗  Falló el arranque, revisa: $FLASK_LOG${RESET}"
  fi
  exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}  ╔══════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}  ║       MangaJaNai Launcher        ║${RESET}"
echo -e "${BOLD}${CYAN}  ╚══════════════════════════════════╝${RESET}"
echo ""

# ── Suwayomi ─────────────────────────────────────────────────────────────────
start_suwayomi() {
  if [ ! -f "$SUWAYOMI_JAR" ]; then
    echo -e "${YELLOW}  ⚠  Suwayomi JAR no encontrado — omitiendo fuentes Mihon${RESET}"
    return
  fi
  if [ -f "$SUWAYOMI_PID" ] && kill -0 "$(cat "$SUWAYOMI_PID")" 2>/dev/null; then
    echo -e "${GREEN}  ✓  Suwayomi ya estaba corriendo (PID $(cat "$SUWAYOMI_PID"))${RESET}"
    return
  fi
  if ! command -v java &>/dev/null; then
    echo -e "${RED}  ✗  Java no encontrado — Suwayomi requiere Java 21+${RESET}"
    return
  fi
  mkdir -p /Manga_Upscaler_project/suwayomi/data
  java -Xmx512m \
    -Dsuwayomi.tachidesk.config.server.rootDir="/Manga_Upscaler_project/suwayomi/data" \
    -Dsuwayomi.tachidesk.config.server.ip="0.0.0.0" \
    -Dsuwayomi.tachidesk.config.server.port="4567" \
    -Dsuwayomi.tachidesk.config.server.systemTrayEnabled="false" \
    -Dsuwayomi.tachidesk.config.server.initialOpenInBrowserEnabled="false" \
    -jar "$SUWAYOMI_JAR" \
    > "$SUWAYOMI_LOG" 2>&1 &
  echo $! > "$SUWAYOMI_PID"
  echo -e "${GREEN}  ✓  Suwayomi iniciado (PID $!)${RESET}"
}

# ── Flask ─────────────────────────────────────────────────────────────────────
start_flask() {
  PYTHON_BIN=""
  if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
  elif [[ -x "$SCRIPT_DIR/.venv/Scripts/python.exe" ]]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/Scripts/python.exe"
  else
    PYTHON_BIN="python"
  fi

  export MANGA_DIR="$SCRIPT_DIR/../../MangaLibrary"
  export UPSCALED_DIR="$SCRIPT_DIR/../../MangaLibrary_Upscaled"

  LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")
  echo ""
  echo -e "${BOLD}  Acceso:${RESET}"
  echo -e "  ${CYAN}→  http://localhost:5001${RESET}"
  [ -n "$LOCAL_IP" ] && echo -e "  ${CYAN}→  http://${LOCAL_IP}:5001${RESET}  (red local / Windows)"
  echo -e "  ${CYAN}→  http://localhost:4567${RESET}  (Suwayomi admin)"
  echo ""
  echo -e "  ${YELLOW}Ctrl+C para detener todo${RESET}"
  echo ""

  echo $$ > "$FLASK_PID"
  cd "$SCRIPT_DIR/src"
  exec "$PYTHON_BIN" -c "
from app import app
app.run(port=5001, debug=False, threaded=True, host='0.0.0.0')
"
}

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
  echo ""
  echo -e "${YELLOW}  Deteniendo servicios...${RESET}"
  if [ -f "$SUWAYOMI_PID" ] && kill -0 "$(cat "$SUWAYOMI_PID")" 2>/dev/null; then
    kill "$(cat "$SUWAYOMI_PID")" 2>/dev/null && echo -e "${GREEN}  ✓  Suwayomi detenido${RESET}"
    rm -f "$SUWAYOMI_PID"
  fi
  rm -f "$FLASK_PID"
  echo -e "${GREEN}  ✓  Flask detenido${RESET}"
  echo ""
}

trap cleanup EXIT INT TERM

# ── Main ──────────────────────────────────────────────────────────────────────
echo -e "  Iniciando servicios...\n"
start_suwayomi
echo -e "${GREEN}  ✓  Flask arrancando en puerto 5001...${RESET}"
start_flask
