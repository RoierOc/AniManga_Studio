#!/usr/bin/env bash
# Descarga Prowlarr + Sonarr + Radarr (binarios .NET AUTOCONTENIDOS: traen su propio runtime,
# así que no hacen falta ni sudo, ni Docker, ni paquetes de AUR) — mismo patrón que suwayomi/
# y flaresolverr/: todo vive dentro del repo y se lanza con start.sh.
#
# qBittorrent NO se instala aquí: ya está en Windows (C:\Program Files\qBittorrent) con la
# WebUI en :8080. El stack lo reutiliza y separa el contenido por CATEGORÍAS, no por cliente.
#
# Las URLs son los endpoints oficiales de actualización de cada proyecto: siempre sirven la
# última versión estable, así que volver a correr este script es también la forma de actualizar.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

fetch_one() {
  local name="$1" url="$2" bin="$3"
  if [ -x "$DIR/$name/$bin" ] && [ "${SERVARR_FORCE:-0}" != "1" ]; then
    echo "[servarr] $name ya instalado (SERVARR_FORCE=1 para reinstalar)"; return 0
  fi
  echo "[servarr] descargando $name…"
  curl -fL --progress-bar "$url" -o "$DIR/$name.tar.gz"
  # Se extrae a un temporal y se cambia de sitio al final: si la descarga o el tar fallan a
  # medias, la instalación que ya funcionaba sigue intacta.
  rm -rf "$DIR/.tmp-$name"; mkdir -p "$DIR/.tmp-$name"
  tar -xzf "$DIR/$name.tar.gz" -C "$DIR/.tmp-$name"
  rm -f "$DIR/$name.tar.gz"
  local extracted
  extracted="$(find "$DIR/.tmp-$name" -maxdepth 1 -mindepth 1 -type d | head -1)"
  [ -x "$extracted/$bin" ] || { echo "[servarr] error: no se encontró $bin en $name" >&2; exit 1; }
  rm -rf "$DIR/$name"
  mv "$extracted" "$DIR/$name"
  rm -rf "$DIR/.tmp-$name"
  echo "[servarr] $name listo"
}

fetch_one Prowlarr "https://prowlarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64" Prowlarr
# Sonarr va por su propio servicio y con otra forma de URL (no comparte el /v1/update/ de
# Radarr/Prowlarr): pide `version=4` explícito y solo responde a GET — un HEAD devuelve 405.
fetch_one Sonarr   "https://services.sonarr.tv/v1/download/main/latest?version=4&os=linux&arch=x64" Sonarr
fetch_one Radarr   "https://radarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64" Radarr

echo "[servarr] hecho. Arranca con: $DIR/start.sh"
