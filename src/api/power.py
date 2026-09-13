"""Apagar este PC desde otro aparato (el móvil).

Módulo propio y no un endpoint suelto en `runtime.py`: es lo único de la API que apaga la
máquina, y quiero poder leer de una sentada TODO lo que puede hacerlo.

Dos cosas que no son adorno:

* **Cuenta atrás en vez de apagar ya.** El aviso de Windows sale en la pantalla del PC y da
  tiempo a `/cancel` desde el móvil. Un botón en un teléfono se pulsa sin querer; un apagado
  no se deshace.
* **409 si hay trabajo en marcha.** Un horneado Anime4K son horas y el apagado se las lleva
  sin decir nada. Se puede forzar, pero hay que pedirlo a propósito.

El apagado de Windows se lleva WSL por delante, así que este proceso muere con él: no hay
teardown ordenado que valga. Por eso el estado se escribe siempre atómico (`write_json_atomic`)
en vez de confiar en un cierre limpio.
"""
from __future__ import annotations

import subprocess

from flask import Blueprint, jsonify, request

from api.platform import is_windows, is_wsl

power_bp = Blueprint('power', __name__)

_SHUTDOWN_WSL = '/mnt/c/Windows/System32/shutdown.exe'

# Un estado que NO está aquí cuenta como «sigue trabajando». Al revés (lista de estados vivos)
# el fallo sería silencioso y del lado malo: un estado nuevo se leería como terminado y el PC
# se apagaría encima de él.
_TERMINADAS = {'complete', 'completed', 'done', 'cancelled', 'canceled',
               'error', 'failed', 'downloaded'}

_ESPERA_POR_DEFECTO = 30
_ESPERA_MAX = 600


def _ocupado() -> list[str]:
    """Qué hay en marcha ahora mismo, en lenguaje humano. Mismo censo que Actividad."""
    from api.status import _all_status

    vivas = []
    for grupo, tareas in _all_status().items():
        for tid, t in (tareas or {}).items():
            if not isinstance(t, dict):
                continue
            if str(t.get('status') or '').lower() in _TERMINADAS:
                continue
            vivas.append(t.get('title') or t.get('name') or f'{grupo}: {tid}')
    return vivas


def _shutdown() -> list[str] | None:
    """El ejecutable de Windows, o None si esta máquina no es Windows ni WSL."""
    if is_wsl():
        return [_SHUTDOWN_WSL]
    if is_windows():
        return ['shutdown']
    return None


def _correr(args: list[str]) -> tuple[bool, str]:
    cmd = _shutdown()
    if cmd is None:
        return False, 'sin_windows'
    try:
        r = subprocess.run(cmd + args, capture_output=True, text=True, timeout=20)
    except Exception as e:                      # interop caído, ejecutable movido…
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or f'shutdown.exe devolvió {r.returncode}').strip()
    return True, ''


def _segundos(datos: dict) -> int:
    try:
        v = int(datos.get('segundos') or _ESPERA_POR_DEFECTO)
    except (TypeError, ValueError):
        v = _ESPERA_POR_DEFECTO
    return max(0, min(_ESPERA_MAX, v))


@power_bp.route('/shutdown', methods=['POST'])
def apagar():
    if _shutdown() is None:
        return jsonify({'error': 'Este equipo no sabe apagarse solo: no es Windows ni WSL'}), 501

    datos = request.get_json(silent=True) or {}
    ocupado = _ocupado()
    if ocupado and not datos.get('forzar'):
        return jsonify({
            'error': 'hay trabajo en marcha',
            # Recortada: con 300 descargas en cola el aviso deja de leerse. El total va aparte.
            'ocupado': ocupado[:8],
            'total': len(ocupado),
        }), 409

    espera = _segundos(datos)
    # Sin acentos a propósito: el aviso lo pinta Windows con su consola, no con UTF-8.
    ok, causa = _correr(['/s', '/t', str(espera), '/c',
                         'AniManga Studio: apagado pedido desde el movil.'])
    if not ok:
        return jsonify({'error': f'Windows no aceptó el apagado: {causa}'}), 500
    return jsonify({'ok': True, 'segundos': espera, 'forzado': bool(ocupado)})


@power_bp.route('/cancel', methods=['POST'])
def cancelar():
    ok, causa = _correr(['/a'])
    if not ok:
        # 1116 = «no hay ningún apagado en curso». No es un fallo: es la respuesta a la pregunta.
        if '1116' in causa:
            return jsonify({'ok': True, 'nada_que_cancelar': True})
        return jsonify({'error': causa}), 500
    return jsonify({'ok': True})
