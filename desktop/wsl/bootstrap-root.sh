#!/usr/bin/env bash
# bootstrap-root.sh — prepara una distro Arch-WSL RECIÉN creada para AniManga Studio.
# Se ejecuta como root (lo lanza provision-engine.ps1 con `wsl -u root`). Es
# autosuficiente: no depende del checkout del repo (todavía no existe) — instala
# git, clona el repo y encadena el bootstrap de usuario.
#
# Idempotente: seguro de re-ejecutar (actualiza en vez de duplicar).
#
# Overridables por env:
#   ANIMANGA_REPO_URL   (default: https://github.com/RoierOc/animanga-studio.git)
#   ANIMANGA_USER       (default: animanga)  — usuario no-root de la app
#   ANIMANGA_DIR        (default: /home/<user>/AniMangaStudio) — ruta del checkout
set -euo pipefail

REPO_URL="${ANIMANGA_REPO_URL:-https://github.com/RoierOc/animanga-studio.git}"
APP_USER="${ANIMANGA_USER:-animanga}"
APP_HOME="/home/$APP_USER"
APP_DIR="${ANIMANGA_DIR:-$APP_HOME/AniMangaStudio}"

say()  { printf '\033[1;34m[bootstrap-root]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[bootstrap-root]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "debe ejecutarse como root (wsl -u root)."

# ── 1. Keyring de pacman (una distro Arch-WSL fresca puede no tenerlo inicializado) ──
if ! pacman-key --list-keys >/dev/null 2>&1; then
    say "inicializando keyring de pacman…"
    pacman-key --init
    pacman-key --populate archlinux
fi

# ── 2. Sistema al día + dependencias ──────────────────────────────────────────
# Comandos → paquetes. Build (obligatorio) y runtime (opcional: si un mirror falla
# no abortamos el resto). qbittorrent-nox da la WebUI de descargas sin GUI.
say "actualizando el sistema (pacman -Syu)…"
pacman -Syu --noconfirm --needed \
    git base-devel python python-pip \
    || fail "no se pudieron instalar las dependencias base."

say "instalando dependencias de runtime (opcionales)…"
pacman -S --noconfirm --needed \
    nodejs pnpm ffmpeg mkvtoolnix-cli jre-openjdk mpv qbittorrent-nox \
    || say "  aviso: alguna dependencia opcional no se instaló — la app arranca igual."

# ── 3. Usuario no-root de la app (con sudo) ───────────────────────────────────
if ! id "$APP_USER" >/dev/null 2>&1; then
    say "creando usuario '$APP_USER'…"
    useradd -m -G wheel -s /bin/bash "$APP_USER"
fi
# sudo sin contraseña para wheel (WSL de un solo usuario, sin GUI de contraseña).
echo '%wheel ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/10-wheel-nopasswd
chmod 0440 /etc/sudoers.d/10-wheel-nopasswd

# ── 4. systemd + usuario por defecto + interop (gotcha binfmt WSLInterop) ──────
# systemd hace que la distro se comporte como un Linux normal (Suwayomi, servicios).
# Al activarlo, el registro binfmt de WSLInterop se pierde salvo que lo re-declaremos
# → sin esto se rompe lanzar mpv.exe/powershell.exe desde WSL (ver memoria del proyecto).
say "configurando /etc/wsl.conf (systemd + usuario por defecto)…"
cat > /etc/wsl.conf <<EOF
[boot]
systemd=true

[user]
default=$APP_USER

[interop]
enabled=true
appendWindowsPath=true
EOF

cat > /etc/binfmt.d/WSLInterop.conf <<'EOF'
:WSLInterop:M::MZ::/init:PF
EOF

# ── 5. Checkout del repo (propiedad del usuario) ──────────────────────────────
if [[ -d "$APP_DIR/.git" ]]; then
    say "repo ya presente en $APP_DIR — git pull…"
    sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only || say "  aviso: git pull falló (sigo con lo que hay)."
else
    say "clonando $REPO_URL → $APP_DIR…"
    sudo -u "$APP_USER" git clone --depth 1 "$REPO_URL" "$APP_DIR" \
        || fail "no se pudo clonar el repositorio."
fi

# ── 6. Encadena el bootstrap de usuario (venv, frontend, assets, CUDA) ─────────
say "lanzando bootstrap de usuario…"
sudo -u "$APP_USER" -H bash "$APP_DIR/desktop/wsl/bootstrap-user.sh"

say "✓ motor preparado en $APP_DIR"
# La ruta se imprime en la última línea para que provision-engine.ps1 la capture
# y la escriba en config.json (linuxPath).
echo "ANIMANGA_LINUX_PATH=$APP_DIR"
