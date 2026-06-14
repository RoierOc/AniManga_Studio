#!/usr/bin/env bash
# Start Suwayomi-Server in headless mode alongside the Flask app

JAR="/Manga_Upscaler_project/suwayomi/Suwayomi-Server.jar"
CONF="/Manga_Upscaler_project/workspace/manga-upscaler/suwayomi/server.conf"
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

mkdir -p /Manga_Upscaler_project/suwayomi/data

java -Xmx512m \
  -Dsuwayomi.tachidesk.config.server.rootDir="/Manga_Upscaler_project/suwayomi/data" \
  -Dsuwayomi.tachidesk.config.server.ip="0.0.0.0" \
  -Dsuwayomi.tachidesk.config.server.port="4567" \
  -Dsuwayomi.tachidesk.config.server.systemTrayEnabled="false" \
  -Dsuwayomi.tachidesk.config.server.initialOpenInBrowserEnabled="false" \
  -jar "$JAR" \
  > "$LOG" 2>&1 &

echo $! > "$PID_FILE"
echo "Suwayomi started — PID $(cat "$PID_FILE") | logs: $LOG"
echo "WebUI: http://localhost:4567"
