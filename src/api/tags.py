#!/usr/bin/env python3
"""Etiquetas propias del usuario sobre obras (manga y anime).

Módulo propio a propósito (regla de documentación técnica): estado nuevo + rutas nuevas = blueprint nuevo,
no engordar `library.py` ni `anime.py`.

Modelo deliberadamente tonto: un único JSON `{kind: {id: [etiquetas]}}`. No hay tabla de
etiquetas, no hay ids de etiqueta, no hay renombrado global — una etiqueta ES su texto. Con una
biblioteca de un usuario esto cabe de sobra en memoria y se lee entero de una vez al arrancar el
frontend, que es lo que evita una petición por tarjeta.

`kind` es 'manga' o 'anime' porque sus identidades no son compatibles: un manga se identifica por
el nombre de su CARPETA y un anime por su id de AniList. Mezclarlos en un solo espacio de nombres
haría que un anime con id "123" chocara con una carpeta llamada "123".
"""

from pathlib import Path

from flask import Blueprint, jsonify, request

from api.observability import record_error
from api.runtime import DATA_ROOT, read_json_safe, write_json_atomic

tags_bp = Blueprint('tags', __name__)

_TAGS_FILE = Path(DATA_ROOT) / 'tags.json'
# 'media' = series y películas occidentales, con identidad `<kind>:<id>` de Sonarr/Radarr — una
# serie 5 y una película 5 son obras distintas, así que el id solo no vale como clave.
_KINDS = ('manga', 'anime', 'media')
_MAX_LEN = 40          # una etiqueta es una etiqueta, no una nota
_MAX_PER_WORK = 12


def _load() -> dict:
    data = read_json_safe(_TAGS_FILE, default={})
    if not isinstance(data, dict):
        return {k: {} for k in _KINDS}
    return {k: (data.get(k) if isinstance(data.get(k), dict) else {}) for k in _KINDS}


def _clean(tags) -> list:
    """Normaliza la lista que manda el cliente. Recorta, quita vacías y duplicadas SIN distinguir
    mayúsculas (que «Releer» y «releer» convivan como dos pills distintos es un bug de cara al
    usuario), y conserva el orden de llegada y la grafía de la primera aparición."""
    if not isinstance(tags, list):
        return []
    out, vistas = [], set()
    for t in tags:
        t = str(t or '').strip()[:_MAX_LEN]
        if not t or t.lower() in vistas:
            continue
        vistas.add(t.lower())
        out.append(t)
        if len(out) >= _MAX_PER_WORK:
            break
    return out


@tags_bp.route('', methods=['GET'])
def get_tags():
    """Todas las etiquetas de una vez. El frontend las quiere enteras para pintar los pills de
    filtro (que necesitan el CONJUNTO de etiquetas, no las de una obra)."""
    return jsonify(_load())


@tags_bp.route('/set', methods=['POST'])
def set_tags():
    body = request.get_json(silent=True) or {}
    kind = str(body.get('kind') or '')
    work = str(body.get('id') or '').strip()
    if kind not in _KINDS or not work:
        return jsonify({'error': f'kind ({"|".join(_KINDS)}) e id son obligatorios'}), 400

    data = _load()
    tags = _clean(body.get('tags'))
    if tags:
        data[kind][work] = tags
    else:
        # Sin etiquetas se BORRA la entrada en vez de dejar una lista vacía: si no, el fichero
        # crece con una fila muerta por cada obra a la que alguna vez le pusiste y quitaste algo.
        data[kind].pop(work, None)

    try:
        write_json_atomic(_TAGS_FILE, data, keep_backup=True)
    except Exception as e:
        # Las etiquetas las escribe el usuario a mano: no se pueden volver a derivar de nada.
        record_error('tags', e, op='set', kind=kind, id=work)
        return jsonify({'error': 'no se pudieron guardar las etiquetas'}), 500
    return jsonify({'ok': True, 'tags': tags})
