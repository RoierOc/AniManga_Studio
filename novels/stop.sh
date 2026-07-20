#!/usr/bin/env bash
pkill -f "novels/sidecar/server.cjs" 2>/dev/null && echo "[novels] detenido" || echo "[novels] no estaba corriendo"
