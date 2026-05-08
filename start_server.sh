#!/usr/bin/env bash
set -euo pipefail

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

export MANGA_DIR="$SCRIPT_DIR/../../MangaLibrary"
export UPSCALED_DIR="$SCRIPT_DIR/../../MangaLibrary_Upscaled"

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
            listenport=5001 listenaddress=0.0.0.0 2>/dev/null || true
        /mnt/c/Windows/System32/netsh.exe interface portproxy add v4tov4 \
            listenport=5001 listenaddress=0.0.0.0 \
            connectport=5001 connectaddress="$WSL2_IP" 2>/dev/null || true
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

cd "$SRC_DIR"
exec "$PYTHON_BIN" -c "
from app import app
app.run(port=5001, debug=False, threaded=True, host='0.0.0.0')
"
