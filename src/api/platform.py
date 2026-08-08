"""OS detection helpers, shared by anime.py, webdav.py, cbz.py and friends.

Consolidates what used to be separately-duplicated `_is_wsl()` /
`_is_wsl2()` checks so there's a single source of truth for "what OS/
environment is this process running under" across the codebase.
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    """True for native Linux *and* WSL (WSL is a Linux kernel)."""
    return sys.platform.startswith("linux")


def lan_ip() -> str:
    """La IP de esta máquina en la red local, para que un móvil sepa a dónde llamar.

    Se abre un socket UDP hacia fuera y se pregunta qué origen eligió el sistema. No se envía
    nada — UDP no conecta de verdad — pero obliga al kernel a resolver la ruta por defecto, que
    es exactamente la pregunta. `gethostbyname(hostname)` no vale: en WSL y con varias interfaces
    devuelve 127.0.0.1 o la interfaz equivocada.
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("192.0.2.1", 9))   # TEST-NET-1: reservada, nunca encaminable de verdad
            return s.getsockname()[0]
    except Exception:
        return ""


def lan_ips() -> list[str]:
    """TODAS las direcciones por las que un dispositivo puede llamar a esta máquina.

    `lan_ip()` sola no basta y el fallo es silencioso del peor tipo: si el móvil está colgado del
    punto de acceso de Windows (192.168.137.x) y le enseñamos la IP de la Ethernet (192.168.0.x),
    la dirección es válida, existe, y simplemente no le responde nunca. Enseñar una lista y que
    elija es mejor que acertar la mitad de las veces.

    En WSL hay que preguntárselo a Windows: el modo `mirrored` sólo replica una interfaz, así que
    desde aquí no se ven ni el hotspot ni las VPN. El resultado se cachea porque esto cuesta ~1 s
    y sólo hace falta al abrir la pantalla de emparejamiento.
    """
    vistas = [ip for ip in (lan_ip(),) if ip]
    if is_wsl():
        vistas += _windows_ipv4()
    fuera = []
    for ip in vistas:
        if ip and not ip.startswith(("127.", "169.254.")) and ip not in fuera:
            fuera.append(ip)
    return fuera


_win_ipv4_cache: list[str] | None = None


def _windows_ipv4() -> list[str]:
    global _win_ipv4_cache
    if _win_ipv4_cache is not None:
        return _win_ipv4_cache
    import subprocess
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-NetIPAddress -AddressFamily IPv4).IPAddress"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        _win_ipv4_cache = [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        # WSLInterop se borra a veces (ver project_wsl_interop_mpv). Sin Windows nos quedamos
        # con lo que ve Linux; nunca con una lista vacía disfrazada de "no hay red".
        _win_ipv4_cache = []
    return _win_ipv4_cache


def first_windows_user_dir() -> Path | None:
    """Return the first real per-user dir under /mnt/c/Users (WSL only),
    skipping the synthetic system accounts Windows always creates."""
    users_root = Path("/mnt/c/Users")
    if not is_wsl() or not users_root.exists():
        return None
    skip = {"All Users", "Default", "Default User", "Public", "TEMP"}
    for user in sorted(users_root.iterdir()):
        if user.name in skip or user.name.startswith("TEMP."):
            continue
        return user
    return None
