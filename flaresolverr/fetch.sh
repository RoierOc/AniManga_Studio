#!/usr/bin/env bash
# Descarga FlareSolverr (binario independiente, trae su propio Chromium) — mismo patrón que
# suwayomi/: nada de sudo ni de demonio de Docker, solo un ejecutable en el repo.
#
# Sirve para las fuentes (manga y novelas) que están detrás del reto de Cloudflare: el sidecar
# de novelas lo usa como reintento ante 403/503 si FLARESOLVERR_URL está definida.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="${FLARESOLVERR_VERSION:-v3.5.0}"
BIN="$DIR/flaresolverr/flaresolverr"

if [ -x "$BIN" ]; then
  echo "[flaresolverr] ya instalado ($BIN)"; exit 0
fi

URL="https://github.com/FlareSolverr/FlareSolverr/releases/download/${VERSION}/flaresolverr_linux_x64.tar.gz"
echo "[flaresolverr] descargando ${VERSION} (~230 MB, incluye Chromium)…"
curl -fL --progress-bar "$URL" -o "$DIR/fs.tar.gz"
tar -xzf "$DIR/fs.tar.gz" -C "$DIR"
rm -f "$DIR/fs.tar.gz"

[ -x "$BIN" ] || { echo "[flaresolverr] error: no se encontró el binario tras extraer" >&2; exit 1; }

# ARREGLO para Arch: el paquete trae una libreadline.so.8 vieja y, como PyInstaller antepone su
# _internal al LD_LIBRARY_PATH, el /bin/sh del sistema (bash 5.3) la carga y muere con
# "undefined symbol: rl_trim_arg_from_keyseq". FlareSolverr lanza `sh` para detectar la versión
# de Chromium, así que concluye "Chrome / Chromium version not detected!" y no arranca.
# Se apunta a la del sistema, que sí tiene el símbolo.
LIB="$DIR/flaresolverr/_internal/libreadline.so.8"
if [ -f "$LIB" ] && [ ! -L "$LIB" ] && [ -e /usr/lib/libreadline.so.8 ]; then
  mv "$LIB" "$LIB.bundled"
  ln -s /usr/lib/libreadline.so.8 "$LIB"
  echo "[flaresolverr] libreadline del sistema enlazada (arreglo Arch)"
fi
echo "[flaresolverr] listo en $BIN"
