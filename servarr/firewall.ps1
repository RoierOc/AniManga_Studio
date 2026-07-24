# Kill switch a nivel de SISTEMA para qBittorrent. Requiere PowerShell como ADMINISTRADOR.
#
# Por qué hace falta si Norton ya trae kill switch: el de Norton es a nivel de APLICACIÓN. En las
# pruebas públicas, al matar el proceso de Norton desde el Administrador de tareas, Windows
# restableció la conexión normal. Esta regla vive en el firewall de Windows: da igual que Norton
# muera, se actualice o se desinstale.
#
# CÓMO FUNCIONA (y por qué no es "bloquear todo y permitir la VPN"):
# En el Firewall de Windows un BLOQUEO siempre gana a un PERMITIR, así que la receta habitual
# "bloquea todo, permite el túnel" no se puede expresar. Lo que sí se puede es bloquear por
# DIRECCIÓN LOCAL de origen: se bloquea qbittorrent.exe cuando su IP de origen es la de tu LAN.
#   - VPN conectada  -> qBittorrent sale con la IP del túnel (10.x/172.x) -> no coincide -> pasa.
#   - VPN caída      -> sale por Ethernet/Wi-Fi con 192.168.0.x          -> coincide  -> bloqueado.
#
# Ejecutar:  powershell -ExecutionPolicy Bypass -File firewall.ps1
#            powershell -ExecutionPolicy Bypass -File firewall.ps1 -Remove   (para deshacer)

param(
    [switch]$Remove,
    [string]$QbtPath = "C:\Program Files\qBittorrent\qbittorrent.exe",
    # Tu LAN. Ethernet (192.0.2.32) y Wi-Fi (192.0.2.33) están las dos aquí dentro:
    # cubrir la subred entera evita tener que perseguir cambios de IP por DHCP.
    [string]$LanSubnet = "192.168.0.0/24"
)

$ErrorActionPreference = "Stop"
$RuleV4 = "qBittorrent - bloquear fuera de VPN (IPv4)"
$RuleV6 = "qBittorrent - bloquear todo IPv6"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Hay que ejecutarlo como ADMINISTRADOR (clic derecho en PowerShell > Ejecutar como administrador)."
    exit 1
}

Get-NetFirewallRule -DisplayName $RuleV4, $RuleV6 -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Remove) {
    Write-Host "Reglas eliminadas. qBittorrent vuelve a poder usar cualquier interfaz." -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $QbtPath)) { Write-Error "No encuentro qbittorrent.exe en $QbtPath"; exit 1 }

# 1) IPv4: bloquear cuando el origen es la LAN (= la VPN no está llevando el tráfico).
New-NetFirewallRule -DisplayName $RuleV4 -Direction Outbound -Action Block `
    -Program $QbtPath -LocalAddress $LanSubnet -Profile Any -Enabled True | Out-Null

# 2) IPv6: bloquear SIEMPRE. El WireGuard de Norton tuneliza IPv4; cualquier IPv6 que salga de
#    qBittorrent iría por fuera del túnel con tu dirección real. Como no hay nada que ganar,
#    se corta entero en vez de intentar afinarlo.
#    Ojo: el Firewall de Windows RECHAZA "::/0" ("prefijo no válido"); "todo IPv6" hay que
#    escribirlo como las dos mitades del espacio de direcciones.
New-NetFirewallRule -DisplayName $RuleV6 -Direction Outbound -Action Block `
    -Program $QbtPath -RemoteAddress "::/1", "8000::/1" -Profile Any -Enabled True | Out-Null

Write-Host "`nReglas creadas:" -ForegroundColor Green
Get-NetFirewallRule -DisplayName $RuleV4, $RuleV6 |
    Select-Object DisplayName, Enabled, Direction, Action | Format-Table -AutoSize

Write-Host "Comprobacion rapida: con la VPN DESCONECTADA, qBittorrent no debe poder anunciar" -ForegroundColor Cyan
Write-Host "a ningun tracker. Con la VPN conectada, si.`n" -ForegroundColor Cyan
