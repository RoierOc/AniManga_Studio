#!/usr/bin/env bash
set -uo pipefail   # -e removed so crash-restart loop works

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"

PYTHON_BIN=""
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif [[ -x "$SCRIPT_DIR/.venv/Scripts/python.exe" ]]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/Scripts/python.exe"
else
    PYTHON_BIN="python"
fi

export MANGA_DIR="/Manga_Upscaler_project/MangaLibrary"
export UPSCALED_DIR="/Manga_Upscaler_project/MangaLibrary_Upscaled"

# Load secrets from .env (if it exists) without polluting the shell
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$SCRIPT_DIR/.env"
    set +a
fi

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

    if [[ "$MIRRORED" == "false" && -n "$WSL2_IP" ]]; then
        # Try portproxy (silent — only works if called from an elevated context)
        /mnt/c/Windows/System32/netsh.exe interface portproxy delete v4tov4 \
            listenport=5100 listenaddress=0.0.0.0 2>/dev/null || true
        /mnt/c/Windows/System32/netsh.exe interface portproxy add v4tov4 \
            listenport=5100 listenaddress=0.0.0.0 \
            connectport=5100 connectaddress="$WSL2_IP" 2>/dev/null || true
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Suwayomi ─────────────────────────────────────────────────────────────────
echo "[start] Iniciando Suwayomi..." >&2
bash "$SCRIPT_DIR/suwayomi/start.sh" >&2

# ── qBittorrent (app Windows) ─────────────────────────────────────────────────
if grep -qi "microsoft" /proc/version 2>/dev/null; then
    if curl -sf --connect-timeout 2 http://localhost:8080/api/v2/app/version >/dev/null 2>&1; then
        echo "[start] qBittorrent ya está corriendo" >&2
    else
        echo "[start] Lanzando qBittorrent..." >&2
        powershell.exe -Command "Start-Process 'C:\Program Files\qBittorrent\qbittorrent.exe'" 2>/dev/null \
            || echo "[start] No se pudo lanzar qBittorrent (puede que ya esté abierto o la ruta cambió)" >&2
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
    print('[server] waitress WSGI — http://127.0.0.1:5101', flush=True)
    serve(app, host='127.0.0.1', port=5101, threads=8)
except ImportError:
    app.run(port=5101, debug=False, threaded=True, host='127.0.0.1')
" || true
    echo "[watchdog] El servidor se detuvo. Reiniciando en 5s..." >&2
    sleep 5
done
