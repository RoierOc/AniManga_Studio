#!/usr/bin/env bash
# Arranca Prowlarr + Sonarr + Radarr. Idempotente: si uno ya está escuchando, no se relanza.
#
# Cada servicio guarda SU configuración (base de datos SQLite, API key, indexers…) en
# servarr/config/<App>, no en ~/.config: así todo el estado del stack vive junto al repo y se
# respalda o se borra de una pieza.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/config" "$DIR/logs"

# nombre:puerto — el puerto es el estándar de cada proyecto, no invento propio.
SERVICES="Prowlarr:9696 Sonarr:8989 Radarr:7878"

up() { # ¿hay algo escuchando ya en ese puerto?
  # `timeout 2` obligatorio: en WSL2 un SYN al loopback contra un puerto CERRADO se descarta
  # (no hay RST) y `/dev/tcp` espera los 6 reintentos del kernel — MEDIDO 2m14s. Sin esto el
  # bucle de espera de abajo no tardaba 60 s sino 60×135 s ≈ 2,2 h con el servicio caído.
  timeout 2 bash -c "(exec 3<>/dev/tcp/127.0.0.1/$1)" 2>/dev/null
}

for svc in $SERVICES; do
  name="${svc%%:*}"; port="${svc##*:}"
  if up "$port"; then echo "[servarr] $name ya está en marcha (:$port)"; continue; fi
  [ -x "$DIR/$name/$name" ] || { echo "[servarr] falta $name — corre ./fetch.sh" >&2; continue; }
  mkdir -p "$DIR/config/$name"
  # setsid: se desliga de esta terminal, así no muere cuando se cierra la sesión.
  setsid "$DIR/$name/$name" -nobrowser -data="$DIR/config/$name" \
    >"$DIR/logs/$name.log" 2>&1 &
  echo "[servarr] $name arrancando… http://localhost:$port"
done

# Espera a que respondan: arrancar el .NET + crear la BD tarda unos segundos la primera vez.
for svc in $SERVICES; do
  name="${svc%%:*}"; port="${svc##*:}"
  for _ in $(seq 1 60); do up "$port" && break; sleep 1; done
  if up "$port"; then echo "[servarr] ✓ $name  → http://localhost:$port"
  else echo "[servarr] ✗ $name no respondió en 60s — mira $DIR/logs/$name.log" >&2; fi
done
