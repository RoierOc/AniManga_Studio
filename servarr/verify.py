#!/usr/bin/env python3
"""Comprueba que el stack está VIVO y bien conectado.

No se limita a mirar si la configuración existe: usa los endpoints /test de cada *arr, que
fuerzan una conexión real contra el servicio de destino. Que un cliente de descarga esté dado
de alta no significa que responda — esa distinción es justo la que oculta los fallos.
"""
import json
import pathlib
import re
import urllib.error
import urllib.request

CFG = pathlib.Path(__file__).resolve().parent / "config"


def apikey(app):
    """Clave leída en caliente del config.xml (nunca escrita en el repo)."""
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", (CFG / app / "config.xml").read_text())
    if not m:
        raise RuntimeError(f"no encuentro la ApiKey de {app} — ¿arrancó alguna vez? (./start.sh)")
    return m.group(1)

OK, BAD, WARN = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"

APPS = {
    "Prowlarr": ("http://localhost:9696", apikey("Prowlarr"), "v1"),
    "Sonarr":   ("http://localhost:8989", apikey("Sonarr"), "v3"),
    "Radarr":   ("http://localhost:7878", apikey("Radarr"), "v3"),
}


def call(url, key=None, data=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"X-Api-Key": key} if key else {}
    if body:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)


print("── Servicios ──")
for name, (url, key, ver) in APPS.items():
    try:
        _, info = call(f"{url}/api/{ver}/system/status", key)
        print(f"{OK} {name:9} v{info['version']:<12} {url}")
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {name:9} no responde: {e}")

try:
    _, v = call("http://localhost:8080/api/v2/app/version")
    print(f"{OK} {'qBittorrent':9} {v:<13} http://localhost:8080  (Windows)")
except Exception as e:  # noqa: BLE001
    print(f"{BAD} qBittorrent no responde: {e}")

print("\n── Conexiones (prueba real, no solo 'está configurado') ──")
for name in ("Sonarr", "Radarr"):
    url, key, ver = APPS[name]
    for client in call(f"{url}/api/{ver}/downloadclient", key)[1]:
        try:
            st, _ = call(f"{url}/api/{ver}/downloadclient/test", key, client)
            print(f"{OK} {name} -> {client['name']} (descargas)")
        except urllib.error.HTTPError as e:
            print(f"{BAD} {name} -> {client['name']}: {e.read().decode()[:200]}")

url, key, ver = APPS["Prowlarr"]
for app in call(f"{url}/api/{ver}/applications", key)[1]:
    try:
        call(f"{url}/api/{ver}/applications/test", key, app)
        print(f"{OK} Prowlarr -> {app['name']} (sincronización de indexers)")
    except urllib.error.HTTPError as e:
        print(f"{BAD} Prowlarr -> {app['name']}: {e.read().decode()[:200]}")

print("\n── Bibliotecas y rutas ──")
for name in ("Sonarr", "Radarr"):
    url, key, ver = APPS[name]
    for r in call(f"{url}/api/{ver}/rootfolder", key)[1]:
        free = r.get("freeSpace") or 0
        acc = OK if r.get("accessible") else BAD
        print(f"{acc} {name}: {r['path']}  ({free / 2**30:.0f} GB libres)")
    for m in call(f"{url}/api/{ver}/remotepathmapping", key)[1]:
        print(f"{OK} {name}: {m['remotePath']} -> {m['localPath']}")

print("\n── Indexers ──")
url, key, ver = APPS["Prowlarr"]
idx = call(f"{url}/api/{ver}/indexer", key)[1]
if idx:
    for i in idx:
        print(f"{OK} {i['name']} ({'activo' if i.get('enable') else 'desactivado'})")
else:
    print(f"{WARN} Ninguno todavía — añádelos en http://localhost:9696 "
          "(Indexers > Add Indexer). Se sincronizarán solos a Sonarr y Radarr.")

print("\n── Avisos de salud ──")
any_issue = False
for name, (url, key, ver) in APPS.items():
    for h in call(f"{url}/api/{ver}/health", key)[1]:
        # 'No indexers available' es lo ESPERADO hasta que añadas los tuyos: no es un fallo.
        if "indexer" in h["message"].lower() and "no indexer" in h["message"].lower():
            continue
        any_issue = True
        print(f"{WARN} {name}: {h['message']}")
if not any_issue:
    print(f"{OK} Sin avisos (aparte de 'faltan indexers', que es lo normal ahora)")
