#!/usr/bin/env bash
# Start Suwayomi-Server in headless mode alongside the Flask app

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAR="${SUWAYOMI_JAR:-$SCRIPT_DIR/Suwayomi-Server.jar}"
CONF="$SCRIPT_DIR/server.conf"
DATA_DIR="${SUWAYOMI_DATA_DIR:-$SCRIPT_DIR/data}"
LOG="/tmp/suwayomi.log"
PID_FILE="/tmp/suwayomi.pid"

if [ ! -f "$JAR" ]; then
  echo "ERROR: Suwayomi JAR not found at $JAR"
  echo "Download from: https://github.com/Suwayomi/Suwayomi-Server/releases"
  exit 1
fi

# Si el puerto 4567 ya responde, Suwayomi está arriba: no arrancar un segundo
# proceso (bloquearía la BD H2). El check de puerto es la fuente de verdad; el
# PID file puede quedar obsoleto entre reinicios de WSL.
if (exec 3<>/dev/tcp/127.0.0.1/4567) 2>/dev/null; then
  echo "Suwayomi ya está escuchando en :4567 — no se relanza."
  exit 0
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Suwayomi already running (PID $(cat "$PID_FILE"))"
  exit 0
fi

# PID file obsoleto (proceso muerto) → limpiarlo para no confundir.
rm -f "$PID_FILE"

mkdir -p "$DATA_DIR"

JAVA_ARGS=(-Xmx512m
  -Dsuwayomi.tachidesk.config.server.rootDir="$DATA_DIR"
  -Dsuwayomi.tachidesk.config.server.ip="0.0.0.0"
  -Dsuwayomi.tachidesk.config.server.port="4567"
  -Dsuwayomi.tachidesk.config.server.systemTrayEnabled="false"
  -Dsuwayomi.tachidesk.config.server.initialOpenInBrowserEnabled="false"
  -jar "$JAR")

# Suwayomi 2.x bundles an embedded Chromium (KCEF WebView, used to solve
# Cloudflare-protected sources) that initializes eagerly on startup and needs
# a real X display on Linux — without one it hangs forever ("Missing X server
# or $DISPLAY") and the HTTP server never binds :4567. xvfb-run gives it a
# fake headless display. Only needed on Linux/WSL with no real $DISPLAY;
# macOS and any environment with a real X session run java directly.
if [[ "$(uname -s)" == "Linux" && -z "${DISPLAY:-}" ]]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    xvfb-run -a java "${JAVA_ARGS[@]}" > "$LOG" 2>&1 &
  else
    echo "AVISO: xvfb-run no instalado — Suwayomi puede colgarse al iniciar KCEF sin \$DISPLAY (instala xvfb)." >&2
    java "${JAVA_ARGS[@]}" > "$LOG" 2>&1 &
  fi
else
  java "${JAVA_ARGS[@]}" > "$LOG" 2>&1 &
fi

echo $! > "$PID_FILE"
echo "Suwayomi started — PID $(cat "$PID_FILE") | logs: $LOG"
echo "WebUI: http://localhost:4567"
