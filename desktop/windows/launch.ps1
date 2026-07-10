# AniManga Studio — lanzador Windows con backend en WSL.
# Instalado por `bash desktop/install.sh` (ejecutado DENTRO de WSL), que copia este
# script + config.json + icono a %LOCALAPPDATA%\AniMangaStudio y crea el acceso
# directo del Menú Inicio. No editar la copia instalada: re-ejecutar el instalador.
#
# Flujo: backend WSL arriba (start_server.sh se auto-detacha con su watchdog) →
# ventana --app de un Chromium de Windows (D3D11: HEVC hw + Anime4K WebGPU a plena
# GPU, sin el problema cross-GPU de Linux híbrido) → cerrar la ventana → /shutdown.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Cfg = Get-Content (Join-Path $Here "config.json") -Raw | ConvertFrom-Json
$Base = "http://127.0.0.1:5101"

function Alive {
    try { (Invoke-WebRequest "$Base/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 }
    catch { $false }
}
function Popup($Msg) {
    (New-Object -ComObject WScript.Shell).Popup($Msg, 0, "AniManga Studio", 48) | Out-Null
}

# Si el server ya corre (lo levantaste a mano en WSL), lo usamos y NO lo apagamos
# al salir — misma semántica que el shell Tauri de Linux (modo desarrollo).
$Owned = -not (Alive)
if ($Owned) {
    # Sin MANGA_SERVER_FG, start_server.sh se relanza detached (nohup) y wsl.exe
    # retorna enseguida; el watchdog queda vivo dentro de la distro.
    # -ArgumentList como ARRAY con comillas embebidas manualmente rompe Start-Process
    # (CreateProcess falla, ExitCode -1) en cuanto un elemento contiene un `"` literal
    # — probado en vivo. Un STRING único sí funciona (Start-Process no re-cita sus
    # trozos), así que las comillas solo hacen falta ahí para rutas con espacios.
    $WslArgs = "-d `"$($Cfg.distro)`" --cd `"$($Cfg.linuxPath)`" -e bash start_server.sh"
    Start-Process -WindowStyle Hidden wsl.exe -ArgumentList $WslArgs
    $Deadline = (Get-Date).AddSeconds(150)   # margen para arranque en frío de WSL
    while (-not (Alive)) {
        if ((Get-Date) -gt $Deadline) {
            Popup ("El backend no respondio en 150 s.`n" +
                   "Log: \\wsl.localhost\$($Cfg.distro)\tmp\manga_server.log")
            exit 1
        }
        Start-Sleep -Milliseconds 500
    }
}

# Navegador Chromium de WINDOWS (nunca el de WSL): config "browser" manda; si no,
# el primero instalado. Edge viene con Windows 11 → siempre hay fallback.
$Candidates = @()
if ($Cfg.PSObject.Properties["browser"] -and $Cfg.browser) { $Candidates += $Cfg.browser }
$Pf = $env:ProgramFiles; $Pf86 = ${env:ProgramFiles(x86)}; $Lad = $env:LOCALAPPDATA
$Candidates += @(
    "$Pf\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$Lad\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$Pf\Google\Chrome\Application\chrome.exe",
    "$Pf86\Google\Chrome\Application\chrome.exe",
    "$Lad\Google\Chrome\Application\chrome.exe",
    "$Pf86\Microsoft\Edge\Application\msedge.exe",
    "$Pf\Microsoft\Edge\Application\msedge.exe"
)
$Browser = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Browser) {
    Popup "No se encontro un navegador Chromium (Brave/Chrome/Edge)."
    exit 1
}

# Perfil propio: proceso dedicado (sin él, --app se fusiona con una instancia ya
# abierta del navegador y perderíamos "cerrar ventana → apagar backend").
$BArgs = @(
    "--app=$Base/",
    "`"--user-data-dir=$Here\webshell`"",   # citado a mano: $Here puede llevar espacios
    "--no-first-run", "--no-default-browser-check",
    # HEVC por hardware en Windows (NVDEC vía D3D11) → /api/stream copia sin recodificar
    "--enable-features=PlatformHEVCDecoderSupport",
    # Chromium 145+ pausa vídeos "poco visibles" — el canvas de Anime4K ocluye el <video>
    "--disable-features=MediaVideoVisibilityTracker,PauseMutedVideos",
    # el player arranca la reproducción por código, sin gesto del usuario
    "--autoplay-policy=no-user-gesture-required"
)
$P = Start-Process $Browser -ArgumentList $BArgs -PassThru
$P.WaitForExit()

if ($Owned -and (Alive)) {
    # exit 0 en Flask → el watchdog lo entiende como apagado limpio y termina.
    try { Invoke-RestMethod -Method Post "$Base/shutdown" -TimeoutSec 5 | Out-Null } catch {}
}
