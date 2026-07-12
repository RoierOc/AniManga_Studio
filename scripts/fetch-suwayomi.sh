#!/usr/bin/env bash
# fetch-suwayomi.sh — descarga el servidor Suwayomi (multi-fuente) si falta.
# Idempotente: si ya existe suwayomi/Suwayomi-Server.jar no hace nada.
#
#   bash scripts/fetch-suwayomi.sh
#
# Override del origen con SUWAYOMI_JAR_URL (por si querés fijar una versión).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say()  { printf '\033[1;34m[suwayomi]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[suwayomi]\033[0m %s\n' "$*" >&2; }

JAR="suwayomi/Suwayomi-Server.jar"
if [[ -f "$JAR" ]]; then
    say "ya está ($JAR) — se omite."
    exit 0
fi

mkdir -p suwayomi

url="${SUWAYOMI_JAR_URL:-}"
if [[ -z "$url" ]]; then
    say "resolviendo la última release…"
    # Toma el primer .jar 'standalone' de la última release (no el instalador).
    url="$(curl -fsSL https://api.github.com/repos/Suwayomi/Suwayomi-Server/releases/latest \
        | grep -oiE '"browser_download_url":[[:space:]]*"[^"]+\.jar"' \
        | grep -iE 'server' | grep -oiE 'https://[^"]+\.jar' | head -n1 || true)"
    # Fallback: cualquier .jar de la release.
    [[ -n "$url" ]] || url="$(curl -fsSL https://api.github.com/repos/Suwayomi/Suwayomi-Server/releases/latest \
        | grep -oiE 'https://[^"]+\.jar' | head -n1 || true)"
fi

if [[ -z "$url" ]]; then
    warn "no pude resolver la URL de descarga (¿sin red / rate-limit de GitHub?)."
    warn "Descargá el .jar standalone de github.com/Suwayomi/Suwayomi-Server/releases"
    warn "y colocalo en $JAR — la app funciona sin él (solo se pierde 'Fuentes')."
    exit 0
fi

say "descargando $url"
tmp="$JAR.part"
if curl -fSL -o "$tmp" "$url"; then
    mv "$tmp" "$JAR"
    say "listo → $JAR"
else
    rm -f "$tmp"
    warn "descarga fallida — la app funciona sin Suwayomi (se pierde 'Fuentes')."
fi
