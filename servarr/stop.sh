#!/usr/bin/env bash
# Para los tres servicios. Se mata por RUTA del binario (no por nombre suelto): así nunca se
# lleva por delante otro proceso que se llame parecido — el mismo cuidado que pide restart.sh.
DIR="$(cd "$(dirname "$0")" && pwd)"
for name in Prowlarr Sonarr Radarr; do
  pids="$(pgrep -f "^$DIR/$name/$name" 2>/dev/null)"
  if [ -n "$pids" ]; then
    echo "$pids" | xargs -r kill
    echo "[servarr] $name parado"
  else
    echo "[servarr] $name no estaba corriendo"
  fi
done
