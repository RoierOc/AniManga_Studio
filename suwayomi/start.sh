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

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Suwayomi already running (PID $(cat "$PID_FILE"))"
  exit 0
fi

mkdir -p /Manga_Upscaler_project/suwayomi/data

java -Xmx512m \
  -Dsuwayomi.tachidesk.config.server.rootDir="/Manga_Upscaler_project/suwayomi/data" \
  -Dsuwayomi.tachidesk.config.server.ip="127.0.0.1" \
  -Dsuwayomi.tachidesk.config.server.port="4567" \
  -Dsuwayomi.tachidesk.config.server.systemTrayEnabled="false" \
  -Dsuwayomi.tachidesk.config.server.initialOpenInBrowserEnabled="false" \
  -jar "$JAR" \
  > "$LOG" 2>&1 &

echo $! > "$PID_FILE"
echo "Suwayomi started — PID $(cat "$PID_FILE") | logs: $LOG"
echo "WebUI: http://localhost:4567"
