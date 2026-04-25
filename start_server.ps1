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

Push-Location $SrcDir
try {
    & $PythonBin -c "from app import app; app.run(port=5001, debug=False, threaded=True, host='0.0.0.0')"
}
finally {
    Pop-Location
}
