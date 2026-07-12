# provision-engine.ps1 — aprovisiona el "motor" WSL de AniManga Studio en un
# equipo Windows nuevo. Lo invoca AniMangaStudio-Setup.exe (o se ejecuta a mano).
#
# Encapsula TODA la complejidad de WSL para que la experiencia sea instalar → usar:
#   1) habilita WSL2 (elevándose y gestionando reinicio si Windows lo pide),
#   2) instala la distro `archlinux`,
#   3) corre el bootstrap dentro (deps, usuario, systemd/binfmt, clona el repo,
#      venv, frontend, CUDA/torch, Suwayomi, modelos, .env),
#   4) escribe config.json con la ruta real del checkout para el shell nativo.
#
# Idempotente y resumible: seguro de re-ejecutar; tras un reinicio retoma solo.
#
# Parámetros (todos opcionales; el Setup pasa -BootstrapScript):
#   -BootstrapScript <ruta>  bootstrap-root.sh que el Setup dejó junto al .exe
#   -Distro <nombre>         distro WSL (default: archlinux)
#   -RepoUrl <url>           repo a clonar dentro de WSL
[CmdletBinding()]
param(
    [string]$BootstrapScript = "",
    [string]$Distro = "archlinux",
    [string]$RepoUrl = "https://github.com/RoierOc/animanga-studio.git",
    [switch]$Resumed
)

$ErrorActionPreference = "Stop"
$AppData = Join-Path $env:LOCALAPPDATA "AniMangaStudio"
New-Item -ItemType Directory -Force -Path $AppData | Out-Null
$Log = Join-Path $AppData "provision.log"
$StateFile = Join-Path $AppData "provision-state.json"

function Say($m) {
    $line = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $m
    Write-Host $line -ForegroundColor Cyan
    Add-Content -Path $Log -Value $line
}
function Warn($m) {
    $line = "[{0:HH:mm:ss}] AVISO: {1}" -f (Get-Date), $m
    Write-Host $line -ForegroundColor Yellow
    Add-Content -Path $Log -Value $line
}
function Popup($m) {
    (New-Object -ComObject WScript.Shell).Popup($m, 0, "AniManga Studio", 48) | Out-Null
}

# Localiza el bootstrap-root.sh si no vino por parámetro (junto a este .ps1).
if (-not $BootstrapScript) {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $cand = @(
        (Join-Path $here "bootstrap-root.sh"),
        (Join-Path $here "..\wsl\bootstrap-root.sh")
    )
    $BootstrapScript = $cand | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $BootstrapScript -or -not (Test-Path $BootstrapScript)) {
    Popup "No se encontró bootstrap-root.sh; no se puede preparar el motor."
    exit 1
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}

# Re-lanza este mismo script ELEVADO (solo se necesita para habilitar WSL2).
function Invoke-Elevated {
    Say "Se requieren permisos de administrador para habilitar WSL2…"
    $argList = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$($MyInvocation.PSCommandPath)`"",
        "-BootstrapScript", "`"$BootstrapScript`"",
        "-Distro", $Distro, "-RepoUrl", $RepoUrl
    )
    $p = Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -PassThru -Wait
    exit $p.ExitCode
}

# WSL utilizable = wsl.exe presente y `wsl --status` responde. `wsl -l -q` sale en
# UTF-16; lo normalizamos para comparar nombres de distro.
function Test-WslReady {
    $wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
    if (-not (Test-Path $wsl)) { return $false }
    try { & $wsl --status *> $null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
}
function Get-Distros {
    $wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
    $prev = $env:WSL_UTF8; $env:WSL_UTF8 = "1"
    try { (& $wsl -l -q) | ForEach-Object { $_.Trim() } | Where-Object { $_ } }
    finally { $env:WSL_UTF8 = $prev }
}

# Programa la reanudación tras un reinicio (RunOnce del usuario) y avisa.
function Schedule-ResumeAfterReboot {
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Normal " +
           "-File `"$($MyInvocation.PSCommandPath)`" -Resumed " +
           "-BootstrapScript `"$BootstrapScript`" -Distro $Distro -RepoUrl `"$RepoUrl`""
    New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" `
        -Name "AniMangaProvision" -Value $cmd -PropertyType String -Force | Out-Null
    '{ "phase": "await-reboot" }' | Set-Content $StateFile
    Say "WSL instalado. Windows necesita REINICIAR para terminar."
    Popup "Se ha instalado WSL2. Reinicia el equipo para terminar la instalacion de AniManga Studio.`n`nAl volver a iniciar sesion, la preparacion continuara sola."
}

# ── Fase 1: WSL2 ──────────────────────────────────────────────────────────────
if (-not (Test-WslReady)) {
    if (-not (Test-Admin)) { Invoke-Elevated }   # se reinvoca elevado y vuelve aquí
    Say "Habilitando WSL2 (wsl --install --no-distribution)…"
    $wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
    & $wsl --install --no-distribution *>> $Log
    & $wsl --set-default-version 2 *>> $Log
    if (-not (Test-WslReady)) {
        # Feature recién habilitada → normalmente exige reinicio.
        Schedule-ResumeAfterReboot
        exit 0
    }
}
Say "WSL2 disponible."

# ── Fase 2: distro archlinux ──────────────────────────────────────────────────
$wsl = Join-Path $env:SystemRoot "System32\wsl.exe"
if (-not ((Get-Distros) -contains $Distro)) {
    Say "Instalando distro '$Distro' (puede tardar)…"
    & $wsl --install $Distro --no-launch *>> $Log
    if ($LASTEXITCODE -ne 0) {
        Popup "No se pudo instalar la distro '$Distro' de WSL. Revisa $Log."
        exit 1
    }
}
Say "Distro '$Distro' presente."

# ── Fase 3: bootstrap dentro de la distro (como root) ─────────────────────────
Say "Preparando el motor dentro de WSL (deps, repo, venv, assets)… puede tardar varios minutos."
# Normaliza CRLF→LF (el .sh viene del FS de Windows) y lo pasa por stdin a bash.
$scriptBody = (Get-Content -Raw $BootstrapScript) -replace "`r`n", "`n"
$linuxPath = ""
try {
    $out = $scriptBody | & $wsl -d $Distro -u root -- env `
        "ANIMANGA_REPO_URL=$RepoUrl" bash -s 2>&1
    $out | ForEach-Object { Add-Content -Path $Log -Value $_ }
    $m = $out | Select-String -Pattern '^ANIMANGA_LINUX_PATH=(.+)$' | Select-Object -Last 1
    if ($m) { $linuxPath = $m.Matches[0].Groups[1].Value.Trim() }
} catch {
    Popup "Fallo preparando el motor en WSL. Revisa $Log."
    exit 1
}
if (-not $linuxPath) {
    Warn "no se pudo determinar la ruta del checkout; usando el default."
    $linuxPath = "/home/animanga/AniMangaStudio"
}
Say "Motor preparado en $Distro:$linuxPath"

# systemd + usuario por defecto de /etc/wsl.conf toman efecto tras reiniciar la distro.
& $wsl --terminate $Distro *>> $Log

# ── Fase 4: diagnóstico GPU (informativo) ─────────────────────────────────────
try {
    $gpu = & $wsl -d $Distro -- bash -lc `
        "cd '$linuxPath' && .venv/bin/python -c 'import torch;print(torch.cuda.is_available())'" 2>&1
    if ("$gpu".Trim() -notmatch "True") {
        Warn "GPU CUDA no disponible en WSL — instala el driver NVIDIA de Windows para el escalado."
    } else { Say "GPU CUDA disponible." }
} catch { Warn "no se pudo verificar la GPU (no bloquea)." }

# ── Fase 5: config.json para el shell nativo ──────────────────────────────────
$cfg = @{ distro = $Distro; linuxPath = $linuxPath } | ConvertTo-Json -Compress
Set-Content -Path (Join-Path $AppData "config.json") -Value $cfg -Encoding UTF8
Say "config.json escrito ($Distro : $linuxPath)."

# Limpieza del estado de reanudación.
Remove-Item $StateFile -ErrorAction SilentlyContinue
Say "✓ Motor listo. Abre AniManga Studio desde el Menú Inicio."
exit 0
