#!/usr/bin/env bash
# Para FlareSolverr (y su Chromium).
pkill -f "flaresolverr/flaresolverr" 2>/dev/null && echo "[flaresolverr] detenido" || echo "[flaresolverr] no estaba corriendo"
