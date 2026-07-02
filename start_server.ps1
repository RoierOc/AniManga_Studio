$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SrcDir = Join-Path $ScriptDir "src"

$PythonBin = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonBin)) {
    $PythonBin = Join-Path $ScriptDir ".venv\bin\python"
}
if (-not (Test-Path $PythonBin)) {
    $PythonBin = "python"
}

# Load .env into the process environment (if present) without polluting the
# user's shell permanently — same secrets start_server.sh reads via `source`.
$EnvFile = Join-Path $ScriptDir ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
}

# Build the frontend if missing (same convenience as start_server.sh).
$DistIndex = Join-Path $ScriptDir "frontend\dist\index.html"
if (-not (Test-Path $DistIndex)) {
    if (Get-Command pnpm -ErrorAction SilentlyContinue) {
        Write-Host "[start] Compilando frontend (primera vez)..."
        Push-Location (Join-Path $ScriptDir "frontend")
        try { pnpm install; pnpm build } finally { Pop-Location }
    } else {
        Write-Host "[start] pnpm no encontrado; se servira la UI antigua en /legacy"
    }
}

# MANGA_DIR/UPSCALED_DIR/MODELS_DIR default to data/ and models/ under the repo
# (see src/api/runtime.py) — override in .env if your data lives elsewhere.
# Suwayomi and qBittorrent auto-launch are WSL/Linux/macOS-only (start_server.sh) —
# on native Windows, start them yourself before running this script if you need them.

Push-Location $SrcDir
try {
    while ($true) {
        & $PythonBin -c "
from app import app
try:
    from waitress import serve
    print('[server] waitress WSGI -- http://127.0.0.1:5101', flush=True)
    serve(app, host='127.0.0.1', port=5101, threads=8)
except ImportError:
    app.run(port=5101, debug=False, threaded=True, host='127.0.0.1')
"
        # Exit 0 = apagado limpio (POST /shutdown desde la app de escritorio): no resucitar.
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[watchdog] Apagado limpio (exit 0). Fin."
            break
        }
        Write-Host "[watchdog] El servidor se detuvo (exit $LASTEXITCODE). Reiniciando en 5s... (Ctrl+C para salir)"
        Start-Sleep -Seconds 5
    }
}
finally {
    Pop-Location
}
