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

cd "$SRC_DIR"
exec "$PYTHON_BIN" -c "
from app import app
app.run(port=5001, debug=False, threaded=True, host='0.0.0.0')
"