"""«Para ti» de ANIME: recomendaciones agregadas sobre tu propia biblioteca.

Por qué existe
─────────────
El manga tenía `/api/anilist/manga/for_you` desde hace tiempo; el anime sólo tenía
recomendaciones POR TÍTULO (`/api/anime/recommendations/<al_id>`), o sea que había que estar
dentro de una ficha para que te sugirieran algo. Medido el 2026-07-31: las 87 series de la
biblioteca llevan `al_id`, así que la señal de gusto estaba entera y sin usar.

Módulo propio y no dentro de `anime.py` por la regla del repo: aquél ya pasa de 4400 líneas.

El método (mismo que el de manga, para que las dos secciones se comporten igual):
  1. Muestrea unas pocas series de la biblioteca — cada consulta a AniList cuesta, y el límite
     ronda las 30/min cuando va degradado.
  2. Suma las recomendaciones de cada una, ponderando por rating y premiando las que aparecen
     recomendadas por VARIAS de tus series (`_seeds`): eso es lo que distingue «te gusta este
     tipo de cosas» de «a alguien le gustó esta serie suelta».
  3. Quita lo que ya tienes.
  4. Cachea el agregado con una huella de la biblioteca: mientras no añadas ni quites nada, no
     se vuelve a barrer.
"""
from __future__ import annotations

import time

from flask import Blueprint, jsonify

from api.imgproxy import hd_url
from api.observability import record_error
from api.runtime import cache_get, cache_set

for_you_bp = Blueprint('for_you', __name__)

# 24 h: las recomendaciones de AniList no se mueven de un día para otro, y el barrido es lo caro.
_TTL = 86400
# Cuántas series de la biblioteca se usan como semilla en UNA jornada. Con 8 el agregado ya es
# estable y el gasto de AniList queda muy por debajo del límite aunque la caché esté fría.
_MUESTRA = 8
# De cuántas se elige esa muestra. Cuanto más ancho el pozo, más se mueven las recomendaciones de
# un día a otro; 24 cubre de sobra una biblioteca como la actual (40 series empezadas).
_POZO = 24


def hoy() -> str:
    """La fecha local, en la forma que se usa como parte de la clave de caché."""
    return time.strftime('%Y-%m-%d')


def rotar(candidatos: list, n: int, dia: str) -> list:
    """Elige `n` candidatos girando la ventana un poco cada DÍA.

    Sin esto, «Para ti» enseñaba exactamente lo mismo hasta que cambiabas la biblioteca: la caché
    dura 24 h pero se recalculaba idéntica, porque las semillas eran siempre las mismas. Girando
    el punto de partida por día, un usuario con 20 series empezadas ve un corte distinto cada
    jornada y las recomendaciones se mueven solas.

    Es una rotación, no un `random`: el mismo día da SIEMPRE el mismo resultado, así que la caché
    sigue sirviendo y no cambia bajo los pies al recargar.
    """
    total = len(candidatos)
    if total <= n:
        return candidatos
    # Número de día absoluto. Con una suma de caracteres de la fecha había colisiones cada pocos
    # días (31-jul y 3-ago daban el MISMO corte); esto es estrictamente creciente.
    try:
        dias = int(time.mktime(time.strptime(dia, '%Y-%m-%d')) // 86400)
    except ValueError:
        dias = 0
    # El paso es el propio tamaño de la ventana: así dos días CONSECUTIVOS no comparten ninguna
    # semilla, en vez de desplazarse una sola posición y recomendar casi lo mismo.
    offset = (dias * n) % total
    girado = candidatos[offset:] + candidatos[:offset]
    return girado[:n]


def _semillas(lib: dict, dia: str) -> list:
    """Las series que mejor representan tu gusto: las que MÁS has visto.

    Sembrar con lo alfabéticamente primero (como hacía el de manga) es estable pero arbitrario:
    una serie que abandonaste en el episodio 1 pesaba igual que una que terminaste. Aquí manda
    lo que de verdad has consumido, y a igualdad, lo más reciente.

    De ese ranking se coge una ventana que ROTA cada día (ver `rotar`), para que las
    recomendaciones no sean las mismas para siempre.
    """
    filas = []
    for a in lib.values():
        al = a.get('al_id')
        if not al:
            continue
        vistos = sum(1 for v in (a.get('watched') or {}).values() if v)
        if not vistos:
            continue
        filas.append((vistos, a.get('last_watched_at') or 0, int(al)))
    filas.sort(reverse=True)
    # Se rota sobre un ranking más ancho que la muestra: si girásemos sólo sobre 8, cada día
    # saldrían las mismas 8 en distinto orden y el agregado no cambiaría nada.
    return rotar([al for _, _, al in filas[:_POZO]], _MUESTRA, dia)


@for_you_bp.route('/anime')
def anime_for_you():
    from api.anime import _lib_read, recs_for_al

    lib = _lib_read() or {}
    if not lib:
        return jsonify([])            # biblioteca vacía: nada que recomendar, no es un fallo

    dia = hoy()
    tengo = {int(a['al_id']) for a in lib.values() if a.get('al_id')}
    semillas = _semillas(lib, dia)
    if not semillas:
        # Tienes series pero no has visto ninguna. No hay señal de gusto todavía; se responde
        # vacío con el motivo, para que la UI diga «ve algo primero» y no «no hay nada».
        return jsonify({'items': [], 'reason': 'sin_historial'})

    # El DÍA entra en la clave: sin él, la caché de 24 h se solapaba con la rotación y podías ver
    # el corte de ayer durante media jornada de hoy.
    fp = str(hash((dia, frozenset(tengo), tuple(semillas))))
    cacheado = cache_get('anime_for_you', fp, _TTL)
    if cacheado is not None:
        # También aquí: el agregado se cachea 24 h con las URLs ya formadas, así que sin esto
        # una entrada guardada antes de la regla de resolución máxima serviría portadas
        # pequeñas durante todo un día. Sanear al leer es más barato que invalidar.
        for r in cacheado:
            r['cover'] = hd_url(r.get('cover') or '')
        return jsonify(cacheado)

    agg: dict = {}
    fallos = 0
    for al in semillas:
        try:
            recs = recs_for_al(al)
        except Exception:
            fallos += 1               # se cuenta y se sigue: una semilla caída no anula el resto
            continue
        for r in recs:
            rid = r.get('al_id')
            if not rid or rid in tengo:
                continue
            e = agg.get(rid)
            peso = (r.get('rating') or 0) + 1
            if e:
                e['_w'] += peso
                e['_seeds'] += 1
            else:
                nuevo = dict(r)
                nuevo['_w'] = peso
                nuevo['_seeds'] = 1
                agg[rid] = nuevo

    if fallos == len(semillas):
        # TODAS fallaron: eso es un fallo de red, no «no hay recomendaciones». Devolverlo como
        # lista vacía sería justo la ambigüedad que el repo prohíbe.
        record_error('for_you', RuntimeError('todas las semillas fallaron'), op='anime', n=fallos)
        return jsonify({'items': [], 'error': 'No se pudo consultar AniList.'}), 502

    ranked = sorted(agg.values(), key=lambda x: (x['_seeds'], x['_w'], x.get('score') or 0), reverse=True)
    for r in ranked:
        r.pop('_w', None)
        r.pop('_seeds', None)
        # La caché de recomendaciones de AniList guarda la URL YA FORMADA, así que las entradas
        # anteriores a la regla de resolución máxima seguirían sirviendo portadas de 230 px para
        # siempre. Normalizar aquí las sana al leerlas, sin invalidar nada ni volver a pedir.
        r['cover'] = hd_url(r.get('cover') or '')
    ranked = ranked[:24]
    cache_set('anime_for_you', fp, ranked, ttl=_TTL, max_entries=10)
    return jsonify(ranked)


@for_you_bp.route('/media')
def media_for_you():
    """«Para ti» de Series y Películas. Mismo método que el de anime, otra fuente: TMDB.

    Aquí la señal de gusto no es «cuántos episodios has visto» (Cine casi no tiene progreso
    todavía) sino la biblioteca entera: haberla añadido YA es una elección. Se siembra con lo
    último añadido, que es lo que más se te parece ahora mismo.
    """
    from api.media import KINDS, _get, _library_tmdb_ids, _tmdb

    dia = hoy()
    semillas, tengo = [], set()
    caidos = []
    for kind, k in KINDS.items():
        try:
            filas = _get(k['app'], k['res'])
        except Exception as exc:
            record_error('for_you', exc, op='media_lib', kind=kind)
            caidos.append(kind)
            continue
        # El id de Sonarr/Radarr crece: los últimos ids son lo último que añadiste.
        filas.sort(key=lambda x: x.get('id') or 0, reverse=True)
        tengo |= {x['tmdbId'] for x in filas if x.get('tmdbId')}
        semillas += [(kind, x['tmdbId']) for x in filas[:_POZO] if x.get('tmdbId')]

    if len(caidos) == len(KINDS):
        return jsonify({'items': [], 'error': 'Sonarr y Radarr no responden.'}), 502
    if not semillas:
        return jsonify({'items': [], 'reason': 'biblioteca_vacia'})

    semillas = rotar(semillas, _MUESTRA, dia)
    fp = str(hash((dia, frozenset(tengo), tuple(semillas))))
    cacheado = cache_get('media_for_you', fp, _TTL)
    if cacheado is not None:
        return jsonify({'items': cacheado})

    agg: dict = {}
    fallos = 0
    for kind, tmdb_id in semillas:
        t = 'tv' if kind == 'series' else 'movie'
        try:
            recs = _tmdb(f'{t}/{tmdb_id}/recommendations').get('results') or []
        except Exception:
            fallos += 1
            continue
        for r in recs:
            rid = r.get('id')
            if not rid or rid in tengo or not r.get('poster_path'):
                continue
            e = agg.get(rid)
            # Una recomendación repetida por VARIAS de tus obras es la señal fuerte; la nota
            # sólo desempata. Mismo criterio que el de anime, para que las dos se comporten igual.
            peso = (r.get('vote_average') or 0) + 1
            if e:
                e['_w'] += peso
                e['_seeds'] += 1
            else:
                agg[rid] = {
                    'tmdb_id': rid, 'kind': kind,
                    'title': r.get('title') or r.get('name') or '',
                    'title_original': r.get('original_title') or r.get('original_name') or '',
                    'poster': f'https://image.tmdb.org/t/p/original{r["poster_path"]}',
                    'year': (r.get('release_date') or r.get('first_air_date') or '')[:4] or None,
                    'score': round(r.get('vote_average') or 0, 1) or None,
                    '_w': peso, '_seeds': 1,
                }

    if fallos == len(semillas):
        record_error('for_you', RuntimeError('todas las semillas fallaron'), op='media', n=fallos)
        return jsonify({'items': [], 'error': 'No se pudo consultar TMDB.'}), 502

    ranked = sorted(agg.values(), key=lambda x: (x['_seeds'], x['_w']), reverse=True)[:24]
    for r in ranked:
        r.pop('_w', None)
        r.pop('_seeds', None)
    cache_set('media_for_you', fp, ranked, ttl=_TTL, max_entries=10)
    return jsonify({'items': ranked})
