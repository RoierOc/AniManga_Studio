#!/usr/bin/env bash
# Construye AniMangaStudio-Setup.exe (Fase 1) — el instalador Windows del shell
# nativo con motor WSL invisible. Se ejecuta DESDE WSL: orquesta cargo.exe (build
# del shell) + ISCC.exe (Inno Setup). En este proyecto el repo vive en WSL y la
# compilación Rust va por cargo.exe de Windows, así que el orquestador es bash
# (no .ps1) para poder sincronizar el crate y lanzar ambas herramientas sin fricción.
#
#   bash desktop/windows/build-installer.sh
#
# Overridables por env: WIN_BUILD_DIR, MPV_SOURCE_WIN, LIBMPV_DIR, DISTRO,
# LINUX_PATH, APP_VERSION.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"      # raíz del repo (WSL)
NATIVE_SRC="$ROOT/desktop/native"

# Config de esta máquina (Fase 1). Fase 2 sustituye esto por detección/provisión.
WIN_BUILD_DIR="${WIN_BUILD_DIR:-/mnt/c/Users/Example/animanga-native/native}"
MPV_SOURCE_WIN="${MPV_SOURCE_WIN:-C:\\Users\\Example\\animanga-native\\libmpv}"
LIBMPV_DIR="${LIBMPV_DIR:-/mnt/c/Users/Example/animanga-native/libmpv}"
DISTRO="${DISTRO:-${WSL_DISTRO_NAME:-archlinux}}"
LINUX_PATH="${LINUX_PATH:-$ROOT}"
APP_VERSION="${APP_VERSION:-0.1.0}"

say()  { printf '\033[1;34m[build-installer]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[build-installer]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. Sync del crate nativo al dir de build de Windows + build release ────────
say "Sync del shell nativo → $WIN_BUILD_DIR"
mkdir -p "$WIN_BUILD_DIR/src"
cp "$NATIVE_SRC"/Cargo.toml "$NATIVE_SRC"/build.rs "$NATIVE_SRC"/animanga.ico "$WIN_BUILD_DIR"/
cp "$NATIVE_SRC"/src/*.rs "$WIN_BUILD_DIR/src/"

say "cargo build --release (shell nativo)…"
# cmd.exe no admite cwd UNC (el repo vive en WSL); entrar al dir /mnt/c del build
# hace que cmd herede un cwd Windows válido (patrón probado).
( cd "$WIN_BUILD_DIR" && cmd.exe /c "set MPV_SOURCE=$MPV_SOURCE_WIN && cargo build --release" ) \
  || fail "cargo build --release falló"

EXE="$WIN_BUILD_DIR/target/release/animanga.exe"
[[ -f "$EXE" ]] || fail "no se generó $EXE"
DLL="$LIBMPV_DIR/libmpv-2.dll"
[[ -f "$DLL" ]] || fail "falta libmpv-2.dll en $LIBMPV_DIR"

# ── 2. Staging ────────────────────────────────────────────────────────────────
STAGING="$WIN_BUILD_DIR/installer-staging"
rm -rf "$STAGING"; mkdir -p "$STAGING/out"
cp "$EXE"                    "$STAGING/animanga.exe"
cp "$DLL"                    "$STAGING/libmpv-2.dll"
cp "$NATIVE_SRC/animanga.ico" "$STAGING/animanga.ico"
printf '{"distro":"%s","linuxPath":"%s"}\n' "$DISTRO" "$LINUX_PATH" > "$STAGING/config.json"
say "config.json baked: distro=$DISTRO linuxPath=$LINUX_PATH"

# ── 3. Bootstrapper WebView2 (silencioso si el runtime falta) ──────────────────
WV="$STAGING/MicrosoftEdgeWebview2Setup.exe"
if [[ ! -f "$WV" ]]; then
  say "Descargando bootstrapper WebView2…"
  curl -fsSL -o "$WV" "https://go.microsoft.com/fwlink/p/?LinkId=2124703" \
    || fail "no se pudo descargar el bootstrapper WebView2"
fi

# ── 4. ISCC (Inno Setup) ──────────────────────────────────────────────────────
# Cubre instalación global (Program Files) y per-user (winget → %LOCALAPPDATA%\Programs).
LAD_WIN="$(cmd.exe /c "echo %LOCALAPPDATA%" 2>/dev/null | tr -d '\r')"
ISCC=""   # ruta WSL (/mnt/c/...) al ISCC.exe — se invoca directo (evita el quoting de cmd)
CANDIDATES=(
  "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"
  "C:\\Program Files\\Inno Setup 6\\ISCC.exe"
)
[[ -n "$LAD_WIN" ]] && CANDIDATES+=("$LAD_WIN\\Programs\\Inno Setup 6\\ISCC.exe")
for c in "${CANDIDATES[@]}"; do
  cwsl="$(wslpath -u "$c" 2>/dev/null || true)"
  [[ -n "$cwsl" && -f "$cwsl" ]] && ISCC="$cwsl" && break
done
[[ -n "$ISCC" ]] || fail "No se encontró Inno Setup (ISCC.exe). Instálalo:  winget install JRSoftware.InnoSetup"

ISS_WIN="$(wslpath -w "$ROOT/desktop/windows/animanga.iss")"
STAGING_WIN="$(wslpath -w "$STAGING")"
say "Compilando instalador con ISCC…"
# Llamada DIRECTA al .exe (no cmd.exe): WSL interop pasa los args tal cual, sin el
# infierno de comillas de cmd. cwd en /mnt/c para que interop no herede un cwd UNC.
( cd /mnt/c && "$ISCC" "/DStaging=$STAGING_WIN" "/DAppVersion=$APP_VERSION" "/DDistro=$DISTRO" "/DLinuxPath=$LINUX_PATH" "$ISS_WIN" ) \
  || fail "ISCC falló al compilar el instalador"

OUT="$STAGING/out/AniMangaStudio-Setup.exe"
[[ -f "$OUT" ]] || fail "no se generó el instalador"
say "✓ Instalador listo: $(wslpath -w "$OUT")"
say "  Pruébalo con doble clic (SmartScreen mostrará aviso de editor desconocido: Más info → Ejecutar de todos modos)."
