# Crea el acceso directo "AniManga Studio" en el Menú Inicio apuntando al
# lanzador ya copiado a %LOCALAPPDATA%\AniMangaStudio (lo copia install.sh).
# Se invoca desde WSL: powershell.exe -File "$(wslpath -w desktop/windows/install-shortcut.ps1)"
$ErrorActionPreference = "Stop"
$Dir = Join-Path $env:LOCALAPPDATA "AniMangaStudio"
if (-not (Test-Path (Join-Path $Dir "launch.ps1"))) {
    throw "falta $Dir\launch.ps1 — ejecuta primero bash desktop/install.sh desde WSL"
}
$Programs = [Environment]::GetFolderPath("Programs")
$Shell = New-Object -ComObject WScript.Shell
$Lnk = $Shell.CreateShortcut((Join-Path $Programs "AniManga Studio.lnk"))
$Lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dir\launch.ps1`""
$Lnk.WorkingDirectory = $Dir
$Lnk.WindowStyle = 7   # minimizada: la consola de PowerShell no parpadea
$Icon = Join-Path $Dir "animanga.ico"
if (Test-Path $Icon) { $Lnk.IconLocation = $Icon }
$Lnk.Save()
Write-Host "[install] Acceso directo creado: $Programs\AniManga Studio.lnk"
