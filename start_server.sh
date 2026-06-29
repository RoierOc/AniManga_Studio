#!/usr/bin/env bash
set -uo pipefail   # -e removed so crash-restart loop works

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"

# ── Auto-detach ───────────────────────────────────────────────────────────────
# Relanza el script en segundo plano (nohup) y devuelve la terminal. Así
# `./start_server.sh` arranca el servidor y te deja seguir usando la consola; el
# server sobrevive al cierre de la terminal. Para correr en primer plano (ver
# logs en vivo, p.ej. para depurar) usá:  MANGA_SERVER_FG=1 ./start_server.sh
if [[ "${MANGA_SERVER_DETACHED:-}" != "1" && "${MANGA_SERVER_FG:-}" != "1" ]]; then
    # Pre-chequeo: si el lock ya está tomado, el server ya corre — avisá y no
    # lances un hijo fantasma que moriría enseguida en el guard de abajo. El
    # flock -n sobre un fd nuevo falla si el server vivo lo tiene en exclusiva.
    if exec 8>/tmp/manga_server.lock && ! flock -n 8; then
        echo "[start] El servidor ya está corriendo (lock tomado). No se lanza otro." >&2
        echo "[start]   web: http://127.0.0.1:5101  |  detener: pkill -f start_server.sh" >&2
        exec 8>&-
        exit 0
    fi
    exec 8>&-
    LOG_FILE="/tmp/manga_server.log"
    MANGA_SERVER_DETACHED=1 nohup "$0" "$@" >"$LOG_FILE" 2>&1 &
    pid=$!
    echo "[start] Servidor lanzado en segundo plano (PID $pid)."
    echo "[start]   logs:    tail -f $LOG_FILE"
    echo "[start]   detener: kill $pid   (o:  pkill -f start_server.sh)"
    echo "[start]   web:     http://127.0.0.1:5101"
    exit 0
fi

# ── Single-instance guard ─────────────────────────────────────────────────────
# Si abres varias terminales WSL a la vez, cada una podría lanzar esta pila antes
# de que Flask ocupe el 5101 → dos Suwayomi compitiendo → BD bloqueada. flock
# garantiza que sólo una instancia de start_server.sh corra a la vez.
exec 9>/tmp/manga_server.lock
if ! flock -n 9; then
    echo "[start] Otra instancia ya está corriendo; no se lanza una segunda." >&2
    exit 0
fi

PYTHON_BIN=""
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif [[ -x "$SCRIPT_DIR/.venv/Scripts/python.exe" ]]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/Scripts/python.exe"
else
    PYTHON_BIN="python"
fi

# MANGA_DIR/UPSCALED_DIR/MODELS_DIR default to data/ and models/ under the repo
# (see src/api/runtime.py) — override in .env if your data lives elsewhere.

# Load secrets from .env (if it exists) without polluting the shell
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ── Preflight: warn (don't fail) about missing external tools ────────────────
# Each one only breaks a specific feature, not the whole app — see docs/INSTALL.md.
_missing=()
for bin in ffmpeg ffprobe mkvmerge java; do
    command -v "$bin" >/dev/null 2>&1 || _missing+=("$bin")
done
if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" && ! -x "$SCRIPT_DIR/.venv/Scripts/python.exe" ]]; then
    _missing+=(".venv (python -m venv .venv && pip install -r requirements.txt)")
fi
command -v pnpm >/dev/null 2>&1 || [[ -f "$SCRIPT_DIR/frontend/dist/index.html" ]] || _missing+=("pnpm (or a pre-built frontend/dist/)")
if [[ ${#_missing[@]} -gt 0 ]]; then
    echo "[start] Aviso: faltan herramientas — algunas funciones no andarán hasta instalarlas (ver docs/INSTALL.md):" >&2
    printf '  - %s\n' "${_missing[@]}" >&2
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── WSL2 network setup ────────────────────────────────────────────────────────
if grep -qi "microsoft" /proc/version 2>/dev/null; then
    WSL2_IP=$(ip addr show 2>/dev/null | grep -oP 'inet \K172\.\d+\.\d+\.\d+' | head -1)
    WIN_IP=$(  /mnt/c/Windows/System32/ipconfig.exe 2>/dev/null \
               | grep -oP '192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+' | head -1 || true)

    # Check if WSL2 mirrored networking is active (WSL IP == Windows LAN IP)
    MIRRORED=false
    if [[ -n "$WIN_IP" && "$WSL2_IP" == "$WIN_IP" ]]; then
        MIRRORED=true
    fi

    # (portproxy eliminado: Flask escucha solo en 127.0.0.1 — no hay nada que reenviar
    #  al exterior. Para acceso desde el móvil el usuario activa acceso LAN en Ajustes.)
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Suwayomi ─────────────────────────────────────────────────────────────────
echo "[start] Iniciando Suwayomi..." >&2
bash "$SCRIPT_DIR/suwayomi/start.sh" >&2

# ── qBittorrent ────────────────────────────────────────────────────────────
# Health-check is OS-agnostic (WebAPI). Auto-launch when absent is just a
# convenience and branches by OS; QBT_LAUNCH_CMD (.env) skips all detection
# for anyone running a different torrent client or setup.
if curl -sf --connect-timeout 2 http://localhost:8080/api/v2/app/version >/dev/null 2>&1; then
    echo "[start] qBittorrent ya está corriendo" >&2
elif [[ -n "${QBT_LAUNCH_CMD:-}" ]]; then
    echo "[start] Lanzando qBittorrent (QBT_LAUNCH_CMD)..." >&2
    eval "$QBT_LAUNCH_CMD" 2>/dev/null || echo "[start] QBT_LAUNCH_CMD falló" >&2
elif grep -qi "microsoft" /proc/version 2>/dev/null; then
    echo "[start] Lanzando qBittorrent (Windows/WSL)..." >&2
    QBT_WIN_PATH="${QBT_WIN_PATH:-C:\\Program Files\\qBittorrent\\qbittorrent.exe}"
    powershell.exe -Command "Start-Process '$QBT_WIN_PATH'" 2>/dev/null \
        || echo "[start] No se pudo lanzar qBittorrent (¿ya está abierto? ¿la ruta cambió? ajustá QBT_WIN_PATH en .env)" >&2
elif [[ "$(uname -s)" == "Darwin" ]]; then
    echo "[start] Lanzando qBittorrent (macOS)..." >&2
    open -a qbittorrent 2>/dev/null || open -a "qBittorrent" 2>/dev/null \
        || echo "[start] No se pudo lanzar qBittorrent — instalalo (brew install --cask qbittorrent) o abrilo manualmente" >&2
else
    echo "[start] Lanzando qBittorrent (Linux)..." >&2
    if command -v qbittorrent-nox >/dev/null 2>&1; then
        nohup qbittorrent-nox >/tmp/qbt.log 2>&1 9>&- &
    elif command -v qbittorrent >/dev/null 2>&1; then
        nohup qbittorrent >/tmp/qbt.log 2>&1 9>&- &
    else
        echo "[start] qBittorrent no encontrado — instalalo (apt/pacman/dnf install qbittorrent-nox) o abrilo manualmente" >&2
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Frontend v2 (Vite) ────────────────────────────────────────────────────────
# Build the SPA if dist is missing. Flask serves frontend/dist at / (legacy at /legacy).
if [[ ! -f "$SCRIPT_DIR/frontend/dist/index.html" ]]; then
    if command -v pnpm >/dev/null 2>&1; then
        echo "[start] Compilando frontend (primera vez)…" >&2
        ( cd "$SCRIPT_DIR/frontend" && pnpm install --silent && pnpm build ) >&2 \
            || echo "[start] Aviso: falló el build del frontend; se servirá /legacy" >&2
    else
        echo "[start] pnpm no encontrado; se servirá la UI antigua en /legacy" >&2
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

cd "$SRC_DIR"

# Watchdog: restart Flask automatically if it exits (e.g. OOM, uncaught exception)
# Ctrl+C stops the loop cleanly.
trap 'echo "[watchdog] Detenido." >&2; exit 0' INT TERM

while true; do
    "$PYTHON_BIN" -c "
from app import app
try:
    from waitress import serve
    print('[server] waitress WSGI — http://0.0.0.0:5101', flush=True)
    serve(app, host='0.0.0.0', port=5101, threads=8)
except ImportError:
    app.run(port=5101, debug=False, threaded=True, host='0.0.0.0')
" || true
    echo "[watchdog] El servidor se detuvo. Reiniciando en 5s..." >&2
    sleep 5
done
