#!/usr/bin/env bash
# fetch-models.sh — descarga los pesos de upscaling que faltan, guiado por
# models/registry.json (la lista de archivos ya versionada). Idempotente.
#
#   bash scripts/fetch-models.sh
#
# Los pesos son binarios pesados y NO están en el repo (.gitignore). Sus URLs de
# descarga oficiales no son estables/directas para todos los modelos, así que el
# script es data-driven y tolerante:
#   - descubre los archivos requeridos leyendo registry.json,
#   - descarga los que falten SI conoce su URL,
#   - para los que no, imprime una guía clara y NO rompe la instalación.
#
# De dónde salen las URLs (en orden de prioridad):
#   1. Variable de entorno por archivo:  MODEL_URL__<archivo con no-alfanum → _>
#        ej.  MODEL_URL__4x_MangaJaNai_1200p_V1_ESRGAN_70k_pth="https://…"
#   2. Archivo scripts/model-urls.env  (KEY=URL, mismo esquema de clave), gitignorado.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say()  { printf '\033[1;34m[models]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[models]\033[0m %s\n' "$*" >&2; }

REG="models/registry.json"
if [[ ! -f "$REG" ]]; then
    warn "no hay $REG — nada que descargar (¿repo incompleto?)."
    exit 0
fi

PY="python"; command -v python >/dev/null 2>&1 || PY="python3"

# Mapa de URLs opcional (gitignorado), formato KEY=URL.
[[ -f scripts/model-urls.env ]] && { set -a; . scripts/model-urls.env; set +a; }

# Lista de archivos únicos referenciados por el registry.
mapfile -t FILES < <("$PY" - "$REG" <<'PY'
import json, sys
reg = json.load(open(sys.argv[1]))
seen = []
for m in reg.values():
    for e in m.get("models", []):
        f = e.get("file")
        if f and f not in seen:
            seen.append(f)
print("\n".join(seen))
PY
)

mkdir -p models
missing=0 fetched=0
for f in "${FILES[@]}"; do
    dest="models/$f"
    if [[ -f "$dest" ]]; then
        continue
    fi
    # Clave de env: no-alfanuméricos → '_'
    key="MODEL_URL__$(printf '%s' "$f" | sed 's/[^A-Za-z0-9]/_/g')"
    url="${!key:-}"
    if [[ -z "$url" ]]; then
        missing=$((missing+1))
        warn "falta '$f' y no tengo URL. Definí $key=<url> (o en scripts/model-urls.env)."
        continue
    fi
    say "descargando $f…"
    if curl -fSL -o "$dest.part" "$url"; then
        mv "$dest.part" "$dest"
        fetched=$((fetched+1))
    else
        rm -f "$dest.part"
        missing=$((missing+1))
        warn "descarga fallida para '$f'."
    fi
done

if (( missing > 0 )); then
    warn "faltan $missing modelo(s). El escalado no funcionará hasta colocarlos en models/."
    warn "Fuentes: OpenModelDB (openmodeldb.info), releases de MangaJaNai/APISR. Ver docs/MODELS.md."
fi
say "modelos: $fetched descargado(s), ${#FILES[@]} referenciado(s) en el registry."
