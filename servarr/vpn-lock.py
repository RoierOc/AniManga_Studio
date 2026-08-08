#!/usr/bin/env python3
"""Ata qBittorrent a la VPN de Norton (WireGuard) para que NO pueda usar la conexión real.

Se aplica por la WebAPI y no editando qBittorrent.ini a propósito: qBittorrent REESCRIBE ese
fichero al cerrarse, así que cualquier edición hecha con la app abierta se pierde en silencio.
Por API se aplica en caliente, persiste y no hace falta cerrar nada.

Dos capas independientes, porque el kill switch de Norton es a nivel de APLICACIÓN (si el
proceso de Norton muere, Windows vuelve a la conexión normal):

  1. Interface binding (aquí): libtorrent solo emite por el adaptador WireGuard. Si el adaptador
     desaparece — que es lo que pasa al desconectar la VPN — no hay ruta alternativa: se queda
     sordo y mudo. Es fail-closed por construcción, no por una comprobación que pueda fallar.
  2. Regla de Firewall de Windows (firewall.ps1): bloquea qbittorrent.exe cuando su IP local
     está en la LAN. Es a nivel de sistema y sobrevive a que Norton se caiga entero.

Uso:
    python3 vpn-lock.py --status     # qué hay ahora
    python3 vpn-lock.py --tune       # ajustes que NO dependen de la VPN (seguro en cualquier momento)
    python3 vpn-lock.py --bind       # ata a la VPN (requiere la VPN CONECTADA)
    python3 vpn-lock.py --unbind     # deshace el binding (para emergencias)
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

QBT = "http://localhost:8080"
# Fragmento que identifica al adaptador de la VPN. Se busca por SUBCADENA y no por nombre exacto
# porque Windows le añade sufijos ("... #2") si el adaptador se recrea.
# Es un DEFAULT, no un candado: si algún día cambias de VPN, `--bind otra` sin tocar el código.
DEFAULT_HINT = "wireguard"     # el protocolo, no la marca: Norton y AVG usan el mismo adaptador


def api(path, data=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    r = urllib.request.Request(f"{QBT}/api/v2/{path}", data=body)
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def prefs():
    return api("app/preferences")


def set_prefs(**kw):
    api("app/setPreferences", {"json": json.dumps(kw)})


def interfaces():
    return api("app/networkInterfaceList")


def find_vpn(hint=DEFAULT_HINT):
    """Devuelve (nombre_visible, valor_interno) del adaptador de la VPN, o None si no está."""
    for i in interfaces():
        if hint.lower() in i["name"].lower():
            return i["name"], i["value"]
    return None


# ── Ajustes que no dependen de la VPN ─────────────────────────────────────────────────────────
# Pensados para MUCHOS torrents sembrando a la vez detrás de una VPN sin port forwarding.
TUNING = {
    # -- Fugas de red --
    # LSD (Local Peer Discovery) manda multicast a la LAN anunciando qué torrents tienes: es
    # tráfico que sale POR FUERA del túnel y además delata tu actividad a la red local. Fuera.
    "lsd": False,
    # UPnP/NAT-PMP le pediría a tu ROUTER REAL que abra puertos: inútil detrás de la VPN
    # (el router no es el extremo del túnel) y abre agujeros en la red que sí quieres proteger.
    "upnp": False,
    # Cifrado preferido, no obligatorio: "obligatorio" corta a los peers que no lo soportan y con
    # Norton ya vas justo de peers al no haber port forwarding.
    "encryption": 1,
    # DHT y PeX SE QUEDAN ENCENDIDOS: los torrents públicos (Nyaa y compañía) dependen de ellos
    # para encontrar peers. Van por dentro del túnel, así que anuncian la IP de la VPN, no la tuya.
    "dht": True,
    "pex": True,
    # Puerto fijo, no aleatorio: con puerto fijo la regla de firewall y el diagnóstico son
    # predecibles. Da igual cuál sea porque de todos modos no se puede abrir en Norton.
    "random_port": False,
    "block_peers_on_privileged_ports": True,
    # -- Volumen: muchos torrents sembrando --
    # Sin port forwarding eres "unconnectable": no te llegan conexiones entrantes, solo puedes
    # salir tú. Por eso conviene MÁS conexiones totales pero MENOS por torrent, para repartirlas
    # entre los 80 en vez de que 3 torrents se coman el cupo.
    "max_connec": 1000,
    "max_connec_per_torrent": 50,
    "max_uploads": 40,
    "max_uploads_per_torrent": 4,
    # Sin cola: quieres los 80 sembrando a la vez, no 5.
    "queueing_enabled": False,
    # -- Disco y memoria para sesiones largas --
    "async_io_threads": 16,
    "file_pool_size": 200,        # 80+ torrents abren muchos ficheros a la vez
    "memory_working_set_limit": 1024,
    "connection_speed": 20,       # algo más suave: WireGuard sufre con ráfagas de conexiones
    # -- Higiene --
    "validate_https_tracker_certificate": True,
    "ssrf_mitigation": True,
    "anonymous_mode": False,      # el binding ya te protege; esto solo rompe trackers
}


def cmd_status():
    p = prefs()
    vpn = find_vpn()
    print("── Adaptador VPN ──")
    print(f"  {'✓ presente: ' + vpn[0] if vpn else '✗ NO presente (¿VPN desconectada?)'}")
    print("\n── Adaptadores que ve qBittorrent ──")
    for i in interfaces():
        print(f"  {i['name']}")
    print("\n── Binding actual ──")
    iface = p["current_network_interface"]
    print(f"  Interfaz : {iface or '(cualquiera — SIN PROTECCIÓN, puede usar tu IP real)'}")
    print(f"  IP fijada: {p['current_interface_address'] or '(todas las del adaptador — correcto)'}")
    print("\n── Ajustes sensibles ──")
    for k in ("lsd", "upnp", "dht", "pex", "encryption", "anonymous_mode",
              "listen_port", "max_connec", "max_connec_per_torrent", "max_uploads"):
        print(f"  {k:24} = {p[k]}")


def cmd_tune():
    print("Aplicando ajustes independientes de la VPN…")
    set_prefs(**TUNING)
    now = prefs()
    bad = {k: (v, now.get(k)) for k, v in TUNING.items() if now.get(k) != v}
    for k, v in TUNING.items():
        print(f"  {'✓' if k not in bad else '✗'} {k} = {now.get(k)}")
    if bad:
        print(f"\n! No se aplicaron: {list(bad)}", file=sys.stderr)
        return 1
    return 0


def cmd_bind(hint=DEFAULT_HINT):
    vpn = find_vpn(hint)
    if not vpn:
        print(f"✗ No hay ningún adaptador cuyo nombre contenga '{hint}'.\n"
              "  Conecta la VPN y reintenta. Si cambiaste de proveedor, pasa el nombre nuevo:\n"
              "    python3 vpn-lock.py --bind mullvad\n"
              "  Adaptadores visibles ahora:", file=sys.stderr)
        for i in interfaces():
            print(f"    - {i['name']}", file=sys.stderr)
        return 1
    name, value = vpn
    # 'current_interface_address' se deja VACÍO adrede = "todas las direcciones de este adaptador".
    # Fijar una IP concreta rompe el binding en cuanto la VPN reconecta y asigna otra IP, y
    # entonces qBittorrent se queda mudo sin motivo aparente. El adaptador es la ancla estable.
    set_prefs(current_network_interface=value, current_interface_name=name,
              current_interface_address="")
    p = prefs()
    ok = p["current_network_interface"] == value
    print(f"{'✓' if ok else '✗'} qBittorrent atado a: {name}")
    print(f"  IP fijada: {p['current_interface_address'] or '(todas las del adaptador — correcto)'}")
    print("\n  A partir de ahora, si ese adaptador desaparece, qBittorrent no tiene\n"
          "  por dónde salir: se queda sin conectividad hasta que la VPN vuelva.")
    return 0 if ok else 1


def cmd_unbind():
    set_prefs(current_network_interface="", current_interface_name="", current_interface_address="")
    print("! Binding retirado: qBittorrent puede volver a usar CUALQUIER interfaz, incluida tu IP real.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--tune", action="store_true")
    g.add_argument("--unbind", action="store_true")
    # `--bind` sin argumento usa Norton; con argumento, cualquier otra VPN ("--bind mullvad").
    g.add_argument("--bind", nargs="?", const=DEFAULT_HINT, metavar="NOMBRE")
    a = ap.parse_args()
    sys.exit(cmd_status() if a.status else cmd_tune() if a.tune
             else cmd_bind(a.bind) if a.bind else cmd_unbind())
