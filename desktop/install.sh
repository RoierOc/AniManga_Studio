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

# En WSL la app se usa desde Windows: no se compila el shell Rust — el lanzador
# es un PowerShell en %LOCALAPPDATA% que levanta el backend WSL y abre una
# ventana --app con un Chromium DE WINDOWS (D3D11: HEVC hw + Anime4K a plena GPU).
IS_WSL=false
grep -qi "microsoft" /proc/version 2>/dev/null && IS_WSL=true

# ── 1. Dependencias del sistema ───────────────────────────────────────────────
# Se comprueban COMANDOS (no nombres de paquete: java/qbittorrent pueden venir
# de distintos paquetes). Las de build son obligatorias; las runtime opcionales
# solo avisan (la app funciona sin ellas, pierdes esa función concreta).
declare -A NEED=(       # comando → paquete pacman sugerido (build, obligatorio)
    [python]=python [node]=nodejs [pnpm]=pnpm
)
if ! $IS_WSL; then
    NEED[cargo]=rust
    NEED[cc]=base-devel
fi
MISSING=()
for c in "${!NEED[@]}"; do command -v "$c" &>/dev/null || MISSING+=("${NEED[$c]}"); done
$IS_WSL || pkg-config --exists webkit2gtk-4.1 2>/dev/null || MISSING+=(webkit2gtk-4.1)
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
# Un venv copiado de otra máquina/ruta trae shebangs y symlinks rotos: si pip no
# arranca, se recrea entero (caso típico: reemplazar la carpeta del proyecto).
if [[ -e .venv ]] && ! .venv/bin/python -m pip --version &>/dev/null; then
    say "venv inválido (¿carpeta copiada de otra máquina?) — recreando…"
    rm -rf .venv
fi
if [[ ! -x .venv/bin/python ]]; then
    say "Creando venv…"
    python -m venv .venv
fi
say "Instalando dependencias Python (puede tardar la primera vez: PyTorch)…"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# ── 3. Frontend ───────────────────────────────────────────────────────────────
say "Compilando frontend…"
# node_modules copiado de otra máquina puede quedar inconsistente con el store
# de pnpm: si el install falla, se regenera desde cero.
( cd frontend && { pnpm install --silent \
    || { rm -rf node_modules && pnpm install --silent; }; } && pnpm build ) >/dev/null

if $IS_WSL; then
    # ── 4W. Lanzador de Windows (backend queda en esta distro WSL) ────────────
    say "WSL detectado — instalando lanzador en Windows…"
    LAD="$(powershell.exe -NoProfile -Command 'Write-Host -NoNewline $env:LOCALAPPDATA' 2>/dev/null | tr -d '\r')"
    [[ -n "$LAD" ]] || fail "no pude leer LOCALAPPDATA (¿interop de Windows activo?)"
    DEST="$(wslpath "$LAD")/AniMangaStudio"
    mkdir -p "$DEST"
    cp desktop/windows/launch.ps1 "$DEST/launch.ps1"
    [[ -n "${WSL_DISTRO_NAME:-}" ]] || fail "WSL_DISTRO_NAME vacía — ejecuta desde una sesión WSL normal"
    printf '{"distro":"%s","linuxPath":"%s"}\n' "$WSL_DISTRO_NAME" "$ROOT" > "$DEST/config.json"
    # Icono .ico para el acceso directo (Pillow ya está en el venv)
    .venv/bin/python - "$DEST/animanga.ico" <<'PY' || say "  aviso: no se pudo generar el icono (la app funciona igual)"
import sys
from PIL import Image
img = Image.open("desktop/src-tauri/icons/icon.png").convert("RGBA")
img.save(sys.argv[1], sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
PY
    powershell.exe -NoProfile -ExecutionPolicy Bypass \
        -File "$(wslpath -w desktop/windows/install-shortcut.ps1)" \
        || fail "no se pudo crear el acceso directo del Menú Inicio"
    say "✓ Instalado. Busca «AniManga Studio» en el Menú Inicio de Windows."
    say "  La ventana usa tu Chromium de Windows (Brave/Chrome/Edge — Edge siempre está)."
    say "  Cerrar la ventana apaga el backend; el lock de WSL evita instancias duplicadas."
    say "  Opcional:"
    say "   - Copia .env.example a .env para credenciales (MangaDex, TMDB…)."
    say "   - Modelos de upscaling: ver docs/MODELS.md (van en models/)."
    say "   - Suwayomi (Fuentes): coloca Suwayomi-Server.jar en suwayomi/ — arranca sola al usarla."
    exit 0
fi

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
