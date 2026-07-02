# Instalador de AniManga Studio (Windows nativo) — idempotente.
#
#   git clone <repo>; cd <repo>; powershell -ExecutionPolicy Bypass -File desktop\install.ps1
#
# Requisitos previos (una vez, con winget):
#   winget install Python.Python.3.12 OpenJS.NodeJS pnpm.pnpm Rustlang.Rustup Git.Git
#   rustup default stable-msvc     (Rust necesita las Build Tools de VS: rustup lo indica si faltan)
# WebView2 ya viene con Windows 11/Edge. qBittorrent/mpv/ffmpeg/mkvtoolnix: instalar aparte si se usan.
#
# NOTA: pendiente de validar en el PC principal (el desarrollo se hace en Arch).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Say($m) { Write-Host "[install] $m" -ForegroundColor Cyan }

# ── 1. Backend Python ─────────────────────────────────────────────────────────
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Say "Creando venv..."
    python -m venv .venv
}
Say "Instalando dependencias Python (PyTorch tarda la primera vez)..."
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

# ── 2. Frontend ───────────────────────────────────────────────────────────────
Say "Compilando frontend..."
Push-Location frontend
try { pnpm install --silent; pnpm build } finally { Pop-Location }

# ── 3. Shell de escritorio (Tauri → WebView2) ────────────────────────────────
Say "Compilando shell de escritorio (Rust)..."
Push-Location desktop\src-tauri
try { cargo build --release } finally { Pop-Location }
$Bin = Join-Path $Root "desktop\src-tauri\target\release\animanga-desktop.exe"
if (-not (Test-Path $Bin)) { throw "no se genero el binario del shell" }

# ── 4. Acceso directo en el Menú Inicio ──────────────────────────────────────
Say "Creando acceso directo..."
$Programs = [Environment]::GetFolderPath("Programs")
$Lnk = Join-Path $Programs "AniManga Studio.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Sc = $Shell.CreateShortcut($Lnk)
$Sc.TargetPath = $Bin
$Sc.WorkingDirectory = $Root
$Sc.IconLocation = "$Bin,0"
$Sc.Save()

Say "OK Instalado. Busca 'AniManga Studio' en el Menu Inicio."
Say "  El shell usa WebView2 (Chromium) en Windows: rendimiento nativo sin configurar nada."
