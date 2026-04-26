#!/usr/bin/env bash
PID_FILE="/tmp/suwayomi.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  kill "$(cat "$PID_FILE")"
  rm "$PID_FILE"
  echo "Suwayomi stopped"
else
  echo "Suwayomi is not running"
fi
