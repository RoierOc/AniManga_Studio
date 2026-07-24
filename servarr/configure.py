#!/usr/bin/env python3
"""Cablea el stack: qBittorrent <- Sonarr/Radarr -> Prowlarr.

IDEMPOTENTE: cada recurso se busca por nombre antes de crearlo, así que correrlo dos veces no
duplica nada. Se hace por API y no a mano por la web para que la configuración sea reproducible
(y para poder rehacerla de cero si algún día se borra servarr/config/).

El punto delicado de esta arquitectura es que qBittorrent corre en WINDOWS y las *arr en WSL:
qBittorrent informa rutas como `D:\\Media\\downloads\\tv` y Sonarr necesita verlas como
`/mnt/d/Media/downloads/tv`. Eso lo resuelve el "remote path mapping" de abajo; sin él, las
descargas terminan bien pero la importación falla con "path does not exist".
"""
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CFG = pathlib.Path(__file__).resolve().parent / "config"


def apikey(app):
    """Lee la clave del config.xml que la propia app se genera al arrancar.

    Se lee en caliente en vez de dejarla escrita aquí: las claves son secretos y este fichero
    va al repo. Además, si se borra servarr/config/ y las apps se regeneran con claves nuevas,
    esto sigue funcionando sin tocar nada.

    Con regex y no con un parser XML a propósito: es un solo campo de un fichero que generamos
    nosotros, y así no se abre la puerta a XXE por usar `xml.etree` sobre un fichero de disco.
    """
    xml = (CFG / app / "config.xml").read_text()
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", xml)
    if not m:
        raise RuntimeError(f"no encuentro la ApiKey de {app} — ¿arrancó alguna vez? (./start.sh)")
    return m.group(1)


QBT = "http://localhost:8080"
APPS = {
    "sonarr": {"url": "http://localhost:8989", "key": apikey("Sonarr"),
               "api": "v3", "root": "/mnt/d/Media/TV", "cat": "tv-sonarr",
               "dl_win": "D:\\Media\\downloads\\tv"},
    "radarr": {"url": "http://localhost:7878", "key": apikey("Radarr"),
               "api": "v3", "root": "/mnt/d/Media/Movies", "cat": "radarr",
               "dl_win": "D:\\Media\\downloads\\movies"},
}
PROWLARR = {"url": "http://localhost:9696", "key": apikey("Prowlarr"), "api": "v1"}


def req(url, key=None, data=None, method=None, form=False):
    body, headers = None, {}
    if key:
        headers["X-Api-Key"] = key
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers, method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def api(app, path, data=None, method=None):
    return req(f"{app['url']}/api/{app['api']}/{path}", app["key"], data, method)


def named(items, name):
    """Busca por nombre — la clave de que esto sea repetible sin duplicar."""
    return next((i for i in items if i.get("name") == name), None)


def step(msg, fn):
    try:
        print(f"  {fn()}  {msg}")
    except urllib.error.HTTPError as e:
        print(f"  ✗ {msg} -> HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - se informa y se sigue con el resto del cableado
        print(f"  ✗ {msg} -> {e}", file=sys.stderr)


# ── 1. Categorías en qBittorrent ─────────────────────────────────────────────────────────────
# Cada *arr descarga a SU carpeta. Mismo disco que la biblioteca a propósito: mover un episodio
# terminado es entonces un rename instantáneo y no una copia (con hardlink se conserva la siembra).
def qbt_categories():
    print("qBittorrent — categorías")
    existing = req(f"{QBT}/api/v2/torrents/categories")
    for app in APPS.values():
        cat, path = app["cat"], app["dl_win"]
        if cat in existing:
            step(f"categoría '{cat}' ya existía", lambda: "=")
            continue
        step(f"categoría '{cat}' -> {path}",
             lambda c=cat, p=path: (req(f"{QBT}/api/v2/torrents/createCategory",
                                        data={"category": c, "savePath": p}, form=True), "+")[1])


# ── 2. Sonarr y Radarr ───────────────────────────────────────────────────────────────────────
def setup_arr(name, app):
    print(f"{name.capitalize()}")

    # Carpeta raíz de la biblioteca (donde queda el contenido ya renombrado y organizado).
    roots = api(app, "rootfolder")
    if any(r["path"].rstrip("/") == app["root"] for r in roots):
        step(f"carpeta raíz {app['root']} ya existía", lambda: "=")
    else:
        step(f"carpeta raíz {app['root']}",
             lambda: (api(app, "rootfolder", {"path": app["root"]}), "+")[1])

    # Cliente de descarga. Host 'localhost' funciona porque WSL está en networkingMode=mirrored;
    # sin eso habría que apuntar a la IP del host Windows.
    clients = api(app, "downloadclient")
    if named(clients, "qBittorrent"):
        step("cliente qBittorrent ya existía", lambda: "=")
    else:
        fields = {"host": "localhost", "port": 8080, "useSsl": False,
                  "username": "", "password": "",
                  ("tvCategory" if name == "sonarr" else "movieCategory"): app["cat"],
                  "recentTvPriority": 0, "olderTvPriority": 0, "initialState": 0,
                  "sequentialOrder": False, "firstAndLast": False, "contentLayout": 0}
        payload = {
            "enable": True, "protocol": "torrent", "priority": 1, "name": "qBittorrent",
            "implementation": "QBittorrent", "configContract": "QBittorrentSettings",
            "fields": [{"name": k, "value": v} for k, v in fields.items()],
        }
        step("cliente qBittorrent -> localhost:8080",
             lambda: (api(app, "downloadclient", payload), "+")[1])

    # EL mapeo Windows->WSL. Se mapea la raíz del disco entero (D:\ -> /mnt/d/) en vez de solo la
    # carpeta de descargas: así sigue valiendo si algún día se añade otra categoría en D:.
    maps = api(app, "remotepathmapping")
    if any(m["remotePath"].rstrip("\\/") == "D:" for m in maps):
        step("mapeo de rutas D:\\ -> /mnt/d/ ya existía", lambda: "=")
    else:
        step("mapeo de rutas D:\\ -> /mnt/d/",
             lambda: (api(app, "remotepathmapping",
                          {"host": "localhost", "remotePath": "D:\\", "localPath": "/mnt/d/"}), "+")[1])


# ── 3. Prowlarr conoce a las otras dos ────────────────────────────────────────────────────────
# Así, cada indexer que añadas EN PROWLARR se sincroniza solo a Sonarr y Radarr: no hay que
# darlo de alta tres veces. Es la razón de ser de Prowlarr.
def prowlarr_apps():
    print("Prowlarr — sincronización de indexers")
    existing = api(PROWLARR, "applications")
    for name, app in APPS.items():
        label = name.capitalize()
        if named(existing, label):
            step(f"aplicación {label} ya existía", lambda: "=")
            continue
        payload = {
            "name": label, "syncLevel": "fullSync",
            "implementation": label, "configContract": f"{label}Settings",
            "fields": [
                {"name": "prowlarrUrl", "value": PROWLARR["url"]},
                {"name": "baseUrl", "value": app["url"]},
                {"name": "apiKey", "value": app["key"]},
                {"name": "syncCategories",
                 "value": [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5090] if name == "sonarr"
                          else [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090]},
            ],
        }
        step(f"aplicación {label} -> {app['url']} (fullSync)",
             lambda p=payload: (api(PROWLARR, "applications", p), "+")[1])


if __name__ == "__main__":
    qbt_categories()
    for n, a in APPS.items():
        setup_arr(n, a)
    prowlarr_apps()
    print("\nListo. Comprueba el estado con: python3 verify.py")
