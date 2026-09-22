#!/usr/bin/env python3
"""Acceso remoto — el backend deja de estar abierto de par en par.

Hoy Waitress sirve en `0.0.0.0:5101` **sin autenticación ninguna**: 272 endpoints, varios de los
cuales escriben ficheros y lanzan procesos, disponibles para cualquiera en la red. Mientras el
único cliente fue el navegador de esta misma máquina eso no se notaba. En cuanto un teléfono habla
con el backend, deja de ser una caja de confianza.

**La regla, en una frase: lo local pasa; lo remoto necesita el token.**

- Lo que llega de 127.0.0.1 / ::1 **por el puerto normal** pasa sin tocar nada. La app de
  escritorio no se entera de que esto existe, y por eso no hay riesgo de dejar al usuario fuera de
  su propia aplicación.
- Lo remoto tiene que presentar el token, por cabecera o por query.

**Dos puertos, no uno.** El servidor escucha además en `PUERTO_REMOTO`, la *boca remota*: lo que
entra por ahí es remoto por definición, diga lo que diga la IP de origen. Sin esa separación, el
puente que hace falta en esta máquina (un `netsh portproxy` de Windows hacia el bucle local, porque
el modo `mirrored` de WSL no entrega tráfico externo) haría que **toda la red pareciera local** —
medido en una tablet real: 200 sin token y `/api/pair` sirviendo el token entero.

Por qué también por query (`?token=`): un reproductor de vídeo o un cargador de imágenes recibe
una URL y la pide él, sin pasar por nuestro código, así que no siempre se le pueden poner
cabeceras. Sin esa vía, las páginas y el vídeo serían inalcanzables desde el móvil.

**Esto NO convierte el servidor en algo publicable en internet.** No hay TLS, ni rotación, ni
límite de intentos: sobre una LAN plana el token viaja en claro. Fuera de casa, VPN
(Tailscale/WireGuard); **nunca abrir el puerto en el router**.

El token vive en `config.json` vía `config_store`, que ya está fuera del perfil sincronizado — un
secreto nunca viaja al repo de sync.
"""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

from api.config_store import get_secret, set_secrets

auth_remote_bp = Blueprint('auth_remote', __name__)

TOKEN_KEY = 'REMOTE_TOKEN'

# Direcciones que se consideran "esta misma máquina". `::ffff:127.0.0.1` es la forma en que un
# socket IPv6 ve una conexión IPv4: omitirla dejaría fuera a la propia app según cómo arranque.
_LOCAL = frozenset({'127.0.0.1', '::1', '::ffff:127.0.0.1'})
_LOCAL_ORIGIN_HOSTS = frozenset({'localhost', '127.0.0.1', '::1', '::ffff:127.0.0.1'})
_MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# La BOCA REMOTA. Todo lo que entra por este puerto se trata como remoto y necesita token, diga lo
# que diga `remote_addr`.
#
# No es una precaución teórica: en esta máquina el modo `mirrored` de WSL no entrega tráfico
# externo, así que la única forma de que un móvil llegue es un `netsh portproxy` de Windows contra
# el bucle local — **y ese puente reescribe el origen a 127.0.0.1**. Medido en un dispositivo real:
# con el puente apuntando al puerto normal, una tablet de la red obtenía 200 sin token y podía leer
# `/api/pair`, o sea el token entero. La conectividad "funcionaba" y la protección se había
# evaporado en silencio.
#
# El puerto por el que ha entrado una petición lo decide el socket que escucha, no el cliente. Es
# la única señal de las tres candidatas en la que se puede confiar: `remote_addr` la reescribe
# cualquier proxy y la cabecera `Host` la pone el cliente.
PUERTO_REMOTO = 5103

# El puerto por el que un dispositivo llama DE VERDAD. En esta máquina es el que escucha el puente
# de Windows (5102 → 127.0.0.1:5103); en una donde el entrante funcione sin puente, es el 5101.
# Sólo se usa para construir las direcciones que enseña la pantalla de emparejamiento: enseñar una
# que no responde es el mismo fallo mudo que enseñar la IP equivocada.
import os as _os
PUERTO_PUBLICO = int(_os.environ.get('REMOTE_PUBLIC_PORT') or 5102)

# Lo único que se responde sin token. `hello` es como el móvil descubre que hay un servidor aquí
# y qué versión habla; si exigiera token, no habría forma de emparejar nada.
_ABIERTO = ('/api/hello',)


def por_la_boca_remota() -> bool:
    """¿Ha entrado por el puerto que sólo alimenta el puente desde la red?"""
    try:
        return int(request.environ.get('SERVER_PORT') or 0) == PUERTO_REMOTO
    except (TypeError, ValueError):
        return False


def es_local(addr: str | None) -> bool:
    """Sólo mira la dirección. Para decidir si una PETICIÓN es local, usa `peticion_local()`."""
    return (addr or '') in _LOCAL


def peticion_local() -> bool:
    """Local de verdad: dirección de esta máquina **y** no llegada por la boca remota."""
    return es_local(request.remote_addr) and not por_la_boca_remota()


def token_actual() -> str:
    """El token, generándolo la primera vez.

    Se genera solo y no se puede desactivar: un token vacío que "desactiva" la protección es la
    clase de opción que un día queda a medias y deja el servidor abierto sin que nadie lo note.
    """
    tok = get_secret(TOKEN_KEY, '')
    if not tok:
        tok = secrets.token_urlsafe(24)
        set_secrets({TOKEN_KEY: tok})
    return tok


def _token_de_la_peticion() -> str:
    cab = request.headers.get('Authorization', '')
    if cab.startswith('Bearer '):
        return cab[7:].strip()
    return request.headers.get('X-Auth-Token') or request.args.get('token') or ''


def guardia():
    """`before_request`: devuelve una respuesta para CORTAR, o None para dejar pasar."""
    if peticion_local():
        return None
    if request.method == 'OPTIONS':
        return None
    if request.path in _ABIERTO:
        return None
    if hmac.compare_digest(_token_de_la_peticion(), token_actual()):
        return None
    # 401 y no 403: le dice al cliente "te faltan credenciales", que es lo que pasa de verdad.
    return jsonify({
        'error': 'token requerido',
        'detalle': 'Este equipo sólo atiende peticiones remotas con token. '
                   'Ajustes → Acceso remoto, en la aplicación de escritorio.',
    }), 401


def _origen_loopback(value: str) -> bool:
    """Acepta solo orígenes HTTP(S) servidos desde la propia máquina."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
    except ValueError:
        return False
    return parsed.scheme.lower() in ('http', 'https') and host in _LOCAL_ORIGIN_HOSTS


def guardia_origen():
    """Bloquea CSRF de navegador contra acciones locales, sin romper clientes directos.

    Las peticiones remotas ya pasaron por ``guardia`` y llevan el token explícito. Los clientes
    locales antiguos o nativos pueden no mandar Origin/Referer, así que ese caso conserva el
    contrato anterior; si un navegador sí declara origen, solo se permite loopback.
    """
    if request.method not in _MUTATING_METHODS or not peticion_local():
        return None
    origin = request.headers.get('Origin', '').strip()
    referer = request.headers.get('Referer', '').strip()
    declared = origin or referer
    if not declared or _origen_loopback(declared):
        return None
    return jsonify({
        'error': 'origen no permitido',
        'detalle': 'Las acciones locales solo aceptan peticiones de la aplicación.',
    }), 403


# ── Endpoints ────────────────────────────────────────────────────────────────

@auth_remote_bp.route('/api/hello')
def hello():
    """Tarjeta de presentación del servidor. Abierta a propósito, sin nada sensible.

    El móvil la usa para dos cosas: confirmar que la IP que le has dado es esta aplicación y no
    otro trasto de la red, y saber qué sabe hacer este servidor antes de pedírselo. `remote_addr`
    va incluido porque es justo el dato que hace falta para depurar "a mí me deja y a ti no".
    """
    return jsonify({
        'app': 'AniManga Studio',
        'api': 1,
        'auth': 'bearer',
        'te_veo_como': request.remote_addr,
        'eres_local': peticion_local(),
        'capacidades': ['manga', 'anime', 'media', 'novels', 'upscale', 'translate', 'subtitles'],
    })


@auth_remote_bp.route('/api/pair')
def pair():
    """El token, para enseñarlo en Ajustes y emparejar un dispositivo.

    **Sólo desde esta máquina.** Si `guardia` ya bloquea lo remoto sin token, pedir el token
    desde fuera sería un absurdo circular; el `es_local` explícito lo deja escrito en el sitio
    donde importa en vez de depender de que el guardián no cambie nunca.
    """
    if not peticion_local():
        return jsonify({'error': 'sólo desde este equipo'}), 403
    from api.platform import lan_ip, lan_ips
    ips = lan_ips()
    ip = lan_ip()
    return jsonify({
        'token': token_actual(),
        # Todas, no una: con un móvil colgado del punto de acceso de Windows, la IP de la
        # Ethernet existe y no responde. Que elija el que sabe dónde está su dispositivo.
        'urls': [f'http://{x}:{PUERTO_PUBLICO}' for x in ips],
        'url': f'http://{ip}:{PUERTO_PUBLICO}' if ip else '',
        'ip': ip,
    })


@auth_remote_bp.route('/api/pair/rotate', methods=['POST'])
def rotate():
    """Invalida el token anterior. Un dispositivo perdido se echa cambiando esto."""
    if not peticion_local():
        return jsonify({'error': 'sólo desde este equipo'}), 403
    nuevo = secrets.token_urlsafe(24)
    set_secrets({TOKEN_KEY: nuevo})
    return jsonify({'token': nuevo})
