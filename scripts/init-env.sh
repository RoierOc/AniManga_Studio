#!/usr/bin/env bash
# init-env.sh — deja un .env listo desde la plantilla, idempotente.
#
#   bash scripts/init-env.sh
#
# - Crea .env desde .env.example si no existe (nunca pisa uno ya editado).
# - Rellena SECRET_KEY con una clave estable generada si está vacía, para que la
#   sesión de Flask no cambie en cada arranque.
# Todas las demás claves quedan vacías: son opcionales y se rellenan luego desde
# Ajustes → Conexiones y claves API (config_store las aplica en caliente).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\033[1;34m[init-env]\033[0m %s\n' "$*"; }

if [[ ! -f .env.example ]]; then
    say "aviso: no hay .env.example — se omite (nada que hacer)."
    exit 0
fi

if [[ ! -f .env ]]; then
    cp .env.example .env
    say "creado .env desde la plantilla."
else
    say ".env ya existe — no se toca."
fi

# Genera SECRET_KEY solo si la línea existe y está vacía (SECRET_KEY=).
if grep -qE '^SECRET_KEY=$' .env; then
    key="$(python -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null \
        || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    # Sustitución en el sitio, portable (evita -i de sed que difiere entre BSD/GNU).
    tmp="$(mktemp)"
    sed "s|^SECRET_KEY=$|SECRET_KEY=${key}|" .env > "$tmp" && mv "$tmp" .env
    say "SECRET_KEY generada."
else
    say "SECRET_KEY ya definida (o sin línea) — no se toca."
fi

say "listo."
