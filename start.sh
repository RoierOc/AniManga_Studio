#!/usr/bin/env bash
# Start everything: Suwayomi + Flask + Windows relay
# Usage:
#   ./start.sh           — foreground (Ctrl+C para parar)
#   ./start.sh --daemon  — background, persiste al cerrar terminal

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUWAYOMI_JAR="/Manga_Upscaler_project/suwayomi/Suwayomi-Server.jar"
SUWAYOMI_LOG="/tmp/suwayomi.log"
SUWAYOMI_PID="/tmp/suwayomi.pid"
FLASK_PID="/tmp/mangajanai-flask.pid"
FLASK_LOG="/tmp/mangajanai-flask.log"
RELAY_PID="/tmp/mangajanai-relay.pid"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'

# ── Windows relay (node.exe): expone puerto 5100 en TODOS los adaptadores ─────
# Flask corre en 127.0.0.1:5101; el relay (proceso Windows nativo) escucha en
# 0.0.0.0:5100 y reenvía — esto cubre WiFi, hotspot y localhost sin portproxy.
start_relay() {
    grep -qi "microsoft" /proc/version 2>/dev/null || return
    local node_bin=""
    if command -v node.exe &>/dev/null; then
        node_bin="node.exe"
    elif [[ -x "/mnt/c/Program Files/nodejs/node.exe" ]]; then
        node_bin="/mnt/c/Program Files/nodejs/node.exe"
    else
        echo -e "${YELLOW}  ⚠  node.exe no encontrado — hotspot puede no funcionar${RESET}"
        return
    fi
    # Kill existing relay
    if [ -f "$RELAY_PID" ] && kill -0 "$(cat "$RELAY_PID")" 2>/dev/null; then
        kill "$(cat "$RELAY_PID")" 2>/dev/null || true
    fi
    local relay_win
    relay_win=$(wslpath -w "$SCRIPT_DIR/relay.js" 2>/dev/null) || return
    "$node_bin" "$relay_win" >/dev/null 2>&1 &
    echo $! > "$RELAY_PID"
    echo -e "${GREEN}  ✓  Relay Windows iniciado (WiFi + Hotspot)${RESET}"
}

DAEMON=false
[[ "${1:-}" == "--daemon" ]] && DAEMON=true

# ── Re-launch as daemon ───────────────────────────────────────────────────────
if $DAEMON; then
  if [ -f "$FLASK_PID" ] && kill -0 "$(cat "$FLASK_PID")" 2>/dev/null; then
    echo -e "${GREEN}  ✓  MangaJaNai ya está corriendo (PID $(cat "$FLASK_PID"))${RESET}"
    echo -e "  ${CYAN}→  http://localhost:5100${RESET}"
    exit 0
  fi
  echo -e "${BOLD}${CYAN}  Iniciando MangaJaNai en background...${RESET}"
  nohup setsid bash "$0" > "$FLASK_LOG" 2>&1 &
  BGPID=$!
  sleep 2
  if kill -0 $BGPID 2>/dev/null; then
    echo -e "${GREEN}  ✓  Corriendo en background (log: ${FLASK_LOG})${RESET}"
    echo -e "  ${CYAN}→  http://localhost:5100${RESET}"
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

  WIN_IP=$(/mnt/c/Windows/System32/ipconfig.exe 2>/dev/null \
           | grep -oP '192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+' | grep -v "137\." | head -1 || true)
  echo ""
  echo -e "${BOLD}  Acceso:${RESET}"
  echo -e "  ${CYAN}→  http://localhost:5100${RESET}              (app principal)"
  [ -n "$WIN_IP" ] && echo -e "  ${CYAN}→  http://${WIN_IP}:5100${RESET}    (WiFi casa)"
  echo -e "  ${CYAN}→  http://192.0.2.1:5100${RESET}         (Mobile Hotspot)"
  echo -e "  ${CYAN}→  http://localhost:5100/library${RESET}      (biblioteca móvil)"
  echo -e "  ${CYAN}→  http://localhost:4567${RESET}              (Suwayomi admin)"
  echo ""
  echo -e "  ${YELLOW}Ctrl+C para detener todo${RESET}"
  echo ""

  echo $$ > "$FLASK_PID"
  cd "$SCRIPT_DIR/src"
  exec "$PYTHON_BIN" -c "
from app import app
app.run(port=5101, debug=False, threaded=True, host='127.0.0.1')
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
  if [ -f "$RELAY_PID" ] && kill -0 "$(cat "$RELAY_PID")" 2>/dev/null; then
    kill "$(cat "$RELAY_PID")" 2>/dev/null && echo -e "${GREEN}  ✓  Relay detenido${RESET}"
    rm -f "$RELAY_PID"
  fi
  rm -f "$FLASK_PID"
  echo -e "${GREEN}  ✓  Flask detenido${RESET}"
  echo ""
}

trap cleanup EXIT INT TERM

# ── Main ──────────────────────────────────────────────────────────────────────
echo -e "  Iniciando servicios...\n"
start_suwayomi
start_relay
echo -e "${GREEN}  ✓  Flask arrancando en puerto 5100...${RESET}"
start_flask
