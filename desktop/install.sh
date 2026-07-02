#!/usr/bin/env bash
# Instalador de AniManga Studio (Linux/Arch) — idempotente, seguro de re-ejecutar.
#
#   git clone <repo> && cd <repo> && bash desktop/install.sh
#
# Hace: dependencias (pacman) → venv Python → frontend (pnpm) → shell (cargo)
# → lanzador de escritorio. Vuelve a ejecutarlo tras un git pull para actualizar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. Dependencias del sistema ───────────────────────────────────────────────
# Se comprueban COMANDOS (no nombres de paquete: java/qbittorrent pueden venir
# de distintos paquetes). Las de build son obligatorias; las runtime opcionales
# solo avisan (la app funciona sin ellas, pierdes esa función concreta).
declare -A NEED=(       # comando → paquete pacman sugerido (build, obligatorio)
    [python]=python [node]=nodejs [pnpm]=pnpm [cargo]=rust [cc]=base-devel
)
MISSING=()
for c in "${!NEED[@]}"; do command -v "$c" &>/dev/null || MISSING+=("${NEED[$c]}"); done
pkg-config --exists webkit2gtk-4.1 2>/dev/null || MISSING+=(webkit2gtk-4.1)
if ((${#MISSING[@]})); then
    say "Faltan paquetes de build: ${MISSING[*]}"
    sudo pacman -S --needed "${MISSING[@]}" || fail "instalación de paquetes cancelada"
else
    say "Dependencias de build OK"
fi
declare -A OPT=(        # comando → para qué sirve (runtime, opcional)
    [ffmpeg]="subtítulos y vídeo (pacman -S ffmpeg)"
    [mkvmerge]="inyección de subtítulos (pacman -S mkvtoolnix-cli)"
    [java]="Fuentes/Suwayomi (pacman -S jre-openjdk)"
    [mpv]="reproductor de anime (pacman -S mpv)"
)
for c in "${!OPT[@]}"; do
    command -v "$c" &>/dev/null || say "  aviso: falta '$c' — ${OPT[$c]}"
done
command -v qbittorrent &>/dev/null || command -v qbittorrent-nox &>/dev/null \
    || say "  aviso: falta qBittorrent — descargas de anime (pacman -S qbittorrent)"

# ── 2. Backend Python ─────────────────────────────────────────────────────────
if [[ ! -x .venv/bin/python ]]; then
    say "Creando venv…"
    python -m venv .venv
fi
say "Instalando dependencias Python (puede tardar la primera vez: PyTorch)…"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# ── 3. Frontend ───────────────────────────────────────────────────────────────
say "Compilando frontend…"
( cd frontend && pnpm install --silent && pnpm build ) >/dev/null

# ── 4. Shell de escritorio ────────────────────────────────────────────────────
say "Compilando shell de escritorio (Rust)…"
( cd desktop/src-tauri && cargo build --release )
BIN="$ROOT/desktop/src-tauri/target/release/animanga-desktop"
[[ -x "$BIN" ]] || fail "no se generó el binario del shell"

# ── 5. Lanzador ───────────────────────────────────────────────────────────────
say "Instalando lanzador de escritorio…"
mkdir -p ~/.local/share/icons/hicolor/256x256/apps ~/.local/share/applications
cp desktop/src-tauri/icons/256x256.png ~/.local/share/icons/hicolor/256x256/apps/animanga-studio.png
cat > ~/.local/share/applications/animanga-studio.desktop <<EOF
[Desktop Entry]
Type=Application
Name=AniManga Studio
Comment=Manga y anime — biblioteca, upscaling y streaming local
Exec=env ANIMANGA_ROOT=$ROOT $BIN
Icon=animanga-studio
Terminal=false
Categories=AudioVideo;Graphics;
StartupWMClass=brave-127.0.0.1__-Default
EOF
update-desktop-database ~/.local/share/applications 2>/dev/null || true

# ── 6. Notas finales ──────────────────────────────────────────────────────────
say "✓ Instalado. Abre «AniManga Studio» desde tu lanzador de aplicaciones."
say "  Opcional:"
say "   - Copia .env.example a .env para credenciales (MangaDex, TMDB…)."
say "   - Modelos de upscaling: ver docs/MODELS.md (van en models/)."
say "   - Suwayomi (Fuentes): coloca Suwayomi-Server.jar en suwayomi/ — arranca sola al usarla."
say "   - mpv con Anime4K para el reproductor externo: pacman -S mpv."
