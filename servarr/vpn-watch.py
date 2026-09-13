#!/usr/bin/env python3
"""Vigila la VPN y vuelve a atar qBittorrent cuando el túnel cambia de IP.

## Qué problema resuelve (y cuál NO)

NO es una protección: la protección ya la da el binding al ADAPTADOR. Si la VPN cae, el adaptador
desaparece y qBittorrent no tiene por dónde salir — se queda mudo. Eso está medido
(`connection_status: disconnected`, 0 nodos DHT) y falla hacia el lado seguro.

El problema real es de DISPONIBILIDAD: al reconectar, Norton asigna una IP de túnel nueva y
qBittorrent sigue escuchando en la vieja. Síntoma medido: trackers anunciando 26-37 seeds y **0
conexiones**, con el adaptador presente y el binding correcto — o sea, todo "bien" y nada
funcionando. Reaplicar el binding lo desbloquea al instante (0 → 852 KB/s).

## Cómo lo detecta

qBittorrent no dice a qué IP ató el socket, así que no se puede preguntar directamente. Se usa la
señal que sí es observable: **la IP del adaptador de la VPN**. Si cambió desde la última vez, el
socket que hay dentro es antiguo por definición → se reaplica el binding.

Correr:  python3 vpn-watch.py            (bucle, para systemd)
         python3 vpn-watch.py --once     (una pasada; útil para probar)
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

QBT = "http://localhost:8080"
# Ni por la marca ni por el protocolo: por VARIAS pistas. Ancla nº1 "norton" → murió al pasar a AVG;
# ancla nº2 "wireguard" → murió el día que el usuario cambió el protocolo a Mimic, que levanta OTRO
# adaptador ("AVG Secure VPN Wintun"). Las dos veces el vigilante leyó "no lo encuentro" como
# "VPN caída" — el estado en que su trabajo es NO hacer nada — y qBittorrent se quedó mudo.
VPN_HINTS = ("vpn", "wireguard", "wintun")
# Túneles que NO son la VPN de salida. Tailscale es una red privada, no una ruta a Internet: atar
# qBittorrent a ella sería tan inútil como atarlo a un adaptador fantasma, y encima silencioso.
NO_VPN = ("tailscale",)
INTERVAL = 20          # s. La reconexión de una VPN tarda segundos: sondear más rápido no gana nada.


def api(path, data=None, timeout=15):
    body = urllib.parse.urlencode(data).encode() if data else None
    r = urllib.request.Request(f"{QBT}/api/v2/{path}", data=body)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def vpn_adapter():
    """(nombre, valor_interno) del adaptador de la VPN, o None si no está."""
    for i in api("app/networkInterfaceList"):
        n = i["name"].lower()
        if any(h in n for h in VPN_HINTS) and not any(x in n for x in NO_VPN):
            return i["name"], i["value"]
    return None


def adapter_ipv4(value):
    """IPv4 actual del adaptador. None si aún no tiene (la VPN está levantando)."""
    addrs = api("app/networkInterfaceAddressList?" + urllib.parse.urlencode({"iface": value}))
    for a in addrs:
        if ":" not in a:          # descarta las IPv6 (incl. link-local fe80::)
            return a
    return None


def rebind(name, value):
    api("app/setPreferences", {"json": json.dumps({
        "current_network_interface": value,
        "current_interface_name": name,
        # Vacío a propósito = "todas las direcciones de este adaptador". Fijar la IP obligaría a
        # perseguirla en cada reconexión, que es justo el fallo que esto arregla.
        "current_interface_address": "",
    })})
    _forzar_relisten()


def _forzar_relisten():
    """Guardar el binding NO basta para que libtorrent vuelva a abrir el socket.

    AVG reutiliza el MISMO GUID de adaptador para WireGuard y para Wintun (Mimic), así que al
    cambiar de protocolo el valor no cambia y qBittorrent concluye que no hay nada que rehacer:
    se queda escuchando en la IP del túnel anterior, que ya no existe. Medido: `disconnected` y
    DHT 0 durante minutos, con las preferencias correctas guardadas y ni una queja en el log.

    Cambiar el puerto y devolverlo sí lo obliga a reabrir. Es un rodeo, pero el único que no pasa
    por desatar el binding — y desatarlo, aunque sea un instante, es exactamente la ventana por la
    que qBittorrent saldría por la conexión real.
    """
    port = api("app/preferences")["listen_port"]
    otro = port + 1 if port < 65000 else port - 1
    for p in (otro, port):
        api("app/setPreferences", {"json": json.dumps({"listen_port": p})})
        time.sleep(3)


def tick(state):
    """Una pasada. `state` guarda la última IP vista. Devuelve el estado nuevo.

    Nunca lanza: si qBittorrent está cerrado (lo normal a ratos), no es un error, es que no hay
    nada que vigilar todavía.
    """
    try:
        vpn = vpn_adapter()
    except (urllib.error.URLError, OSError) as e:
        if state.get("qbt_up", True):
            log(f"· qBittorrent no responde ({e}); esperando")
        return {**state, "qbt_up": False}

    state = {**state, "qbt_up": True}
    if not vpn:
        if state.get("ip") is not None:
            log("✗ VPN caída — qBittorrent queda sin salida (esto es lo correcto)")
        return {**state, "ip": None}

    name, value = vpn
    try:
        ip = adapter_ipv4(value)
    except (urllib.error.URLError, OSError):
        return state
    if not ip:
        return state          # adaptador presente pero sin IP: la VPN aún está levantando

    # Guardia de EXPOSICIÓN, aparte del cambio de IP: si el binding desapareció (una actualización
    # de qBittorrent, un reset de preferencias, un `--unbind` olvidado), qBittorrent podría salir
    # por la conexión real. Aquí sí importa la seguridad, no la disponibilidad: se restaura y se
    # deja constancia, porque un binding que se cae en silencio es justo lo que no se puede tolerar.
    #
    # Se comparan las DOS mitades del binding. El valor (`iftype53_NNNNN`) es una RANURA que se
    # recicla: al cambiar de protocolo, el `iftype53_32769` de WireGuard pasó a ser el del Wintun
    # y el valor casaba solo. Pero libtorrent ata por NOMBRE, que seguía diciendo "…WireGuard" →
    # `disconnected`, DHT 0, cero descargas, sin una sola línea de queja. Mirar solo el valor
    # habría dado el binding por bueno. (Mismo patrón que el mangaId efímero de Suwayomi.)
    try:
        prefs = api("app/preferences")
        bound, bound_name = (prefs.get("current_network_interface", ""),
                             prefs.get("current_interface_name", ""))
    except (urllib.error.URLError, OSError):
        return state
    if bound != value or bound_name != name:
        rebind(name, value)
        log(f"!! BINDING PERDIDO (estaba en {bound_name or '(cualquier interfaz)'}"
            f"/{bound or '-'}) — restaurado a {name}/{value}")
        return {**state, "ip": ip}

    if ip != state.get("ip"):
        # Cubre los dos casos con la misma acción: primera vez que se ve la VPN, y reconexión
        # con IP nueva. En ambos el socket que hay dentro de qBittorrent está obsoleto.
        prev = state.get("ip")
        rebind(name, value)
        log(f"✓ Reatado a {name} — IP del túnel {prev or '(ninguna)'} → {ip}")
    return {**state, "ip": ip}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="una sola pasada y salir")
    a = ap.parse_args()

    if a.once:
        s = tick({})
        log(f"estado: {s}")
        return 0

    log(f"vigilando la VPN cada {INTERVAL}s "
        f"(adaptador que contenga {'/'.join(VPN_HINTS)} y no {'/'.join(NO_VPN)})")
    state = {}
    while True:
        try:
            state = tick(state)
        except Exception as e:                      # nunca morir: es un servicio de fondo
            log(f"! error inesperado, sigo: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
