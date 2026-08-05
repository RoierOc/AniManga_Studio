"""Retrospectiva: «tu mes» / «tu año» a partir del historial.

Por qué separa VISTO de MARCADO
───────────────────────────────
Medido sobre el historial real el 2026-07-31, mirando el hueco entre dos registros consecutivos
de la MISMA serie:

    < 60 s     433        ← imposible: un episodio dura ~24 min
    60-180 s     3
    180-900 s    5
    >= 900 s    42        ← esto sí es ver la serie

La MEDIANA del hueco es **5 segundos**. O sea que la inmensa mayoría de las 500 entradas no son
visionados sino marcados en masa (marcar una temporada entera como vista de una vez). Un resumen
que dijera «has visto 500 episodios en 21 días» sería falso y el usuario lo notaría al instante,
que es la peor forma de estrenar una función.

Así que se cuentan las dos cosas por separado y se enseñan las dos. El número grande es el de
visionados reales; los marcados se reportan aparte, sin esconderlos.

`GAP_REAL` es la única constante que decide esto, y está deliberadamente sola y con nombre: es un
umbral físico (lo que tarda en verse un episodio), no un número mágico ajustado a un dataset.
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict

from flask import Blueprint, jsonify, request

from api import history_store
from api.imgproxy import hd_url

retro_bp = Blueprint('retrospective', __name__)

# Hueco mínimo entre dos episodios de la misma serie para considerar que se VIERON, no que se
# marcaron. 10 min es conservador: un episodio dura ~24, y así un salto de opening/créditos o
# un abandono a la mitad siguen contando como visionado.
GAP_REAL = 600

PERIODOS = {'mes': 30 * 86400, 'anio': 365 * 86400, 'todo': 0}

# Duración típica de un episodio cuando no se sabe la real. Sólo se usa para el total de horas,
# y el frontend avisa de que es una estimación.
DUR_POR_DEFECTO = 1420


def _clasificar(entradas: list) -> tuple[list, int]:
    """Parte el historial en (visionados reales, nº de marcados en masa).

    Se recorre de más antiguo a más nuevo llevando la última marca POR SERIE: una entrada cuenta
    como visionado si han pasado al menos `GAP_REAL` desde la anterior de esa misma serie. La
    primera de cada serie siempre cuenta (no hay con qué compararla).
    """
    orden = sorted(entradas, key=lambda e: int(e.get('watched_at') or 0))
    ultima: dict = {}
    reales, marcados = [], 0
    for e in orden:
        sid = str(e.get('anime_id') or '')
        ts = int(e.get('watched_at') or 0)
        anterior = ultima.get(sid)
        if anterior is None or (ts - anterior) >= GAP_REAL:
            reales.append(e)
        else:
            marcados += 1
        ultima[sid] = ts
    return reales, marcados


def _racha(dias: set) -> tuple[int, str]:
    """Racha más larga de días CONSECUTIVOS con actividad, y el día en que terminó."""
    if not dias:
        return 0, ''
    ordenados = sorted(dias)
    mejor, actual, fin_mejor = 1, 1, ordenados[0]
    for i in range(1, len(ordenados)):
        ayer = time.strftime('%Y-%m-%d', time.localtime(
            time.mktime(time.strptime(ordenados[i], '%Y-%m-%d')) - 86400))
        if ordenados[i - 1] == ayer:
            actual += 1
        else:
            actual = 1
        if actual > mejor:
            mejor, fin_mejor = actual, ordenados[i]
    return mejor, fin_mejor


def _duraciones(lib: dict) -> dict:
    """{anime_id: {ep: segundos}} — lo que ya se sabe de cada episodio."""
    out = {}
    for aid, a in (lib or {}).items():
        d = a.get('durations') or {}
        if d:
            out[str(aid)] = {str(k): int(v or 0) for k, v in d.items()}
    return out


@retro_bp.route('')
def retrospectiva():
    """Resumen del periodo. `?periodo=mes|anio|todo` (por defecto, el año)."""
    from api.anime import _lib_read

    periodo = request.args.get('periodo', 'anio')
    if periodo not in PERIODOS:
        periodo = 'anio'
    ventana = PERIODOS[periodo]
    desde = int(time.time()) - ventana if ventana else 0

    vistos_raw = history_store.read_all('watch', since=desde)
    leidos = history_store.read_all('reading', since=desde)

    reales, marcados = _clasificar(vistos_raw)

    lib = _lib_read() or {}
    durs = _duraciones(lib)

    # ── Tiempo ────────────────────────────────────────────────────────────
    segundos = 0
    estimados = 0
    for e in reales:
        # La entrada MANDA sobre la biblioteca: series y películas la traen del reproductor, que
        # es quien conoce la duración de verdad. Sin esto, una película de 2 h 46 contaba como un
        # episodio de anime de 23,7 min — el resumen del año se quedaría corto por horas.
        d = int(e.get('duration') or 0) or durs.get(str(e.get('anime_id')), {}).get(str(e.get('episode')))
        if d:
            segundos += d
        else:
            segundos += DUR_POR_DEFECTO
            estimados += 1

    # ── Por serie ─────────────────────────────────────────────────────────
    por_serie = Counter()
    portada = {}
    for e in reales:
        sid = str(e.get('anime_id') or '')
        por_serie[sid] += 1
        if e.get('cover') and sid not in portada:
            portada[sid] = e['cover']
    titulo = {}
    for e in reales:
        sid = str(e.get('anime_id') or '')
        if sid not in titulo and e.get('title'):
            titulo[sid] = e['title']

    top = [{
        'id': sid,
        'title': titulo.get(sid, sid),
        # Portada a la mayor resolución: el historial guarda la URL formada, que puede ser vieja.
        'cover': hd_url(portada.get(sid, '')),
        'episodes': n,
    } for sid, n in por_serie.most_common(5)]

    # ── Ritmo ─────────────────────────────────────────────────────────────
    por_dia = defaultdict(int)
    por_hora = Counter()
    por_semana = Counter()
    for e in reales:
        ts = int(e.get('watched_at') or 0)
        if not ts:
            continue
        t = time.localtime(ts)
        por_dia[time.strftime('%Y-%m-%d', t)] += 1
        por_hora[t.tm_hour] += 1
        por_semana[t.tm_wday] += 1

    dia_top = max(por_dia.items(), key=lambda kv: kv[1]) if por_dia else ('', 0)
    racha, racha_fin = _racha(set(por_dia))

    # Lectura: el historial de manga no tiene el problema del marcado en masa (una entrada por
    # capítulo terminado), así que se cuenta tal cual.
    obras = {e.get('title') for e in leidos if e.get('title')}

    return jsonify({
        'periodo': periodo,
        'desde': desde,
        'anime': {
            'episodes': len(reales),
            'marked': marcados,           # marcados en masa, NO visionados. Se enseña aparte.
            'series': len(por_serie),
            'seconds': segundos,
            'estimated': estimados,       # cuántos episodios usaron duración por defecto
            'top': top,
        },
        'manga': {
            'chapters': len(leidos),
            'works': len(obras),
        },
        'rhythm': {
            'days': len(por_dia),
            'streak': racha,
            'streak_end': racha_fin,
            'best_day': {'date': dia_top[0], 'episodes': dia_top[1]},
            'by_hour': [por_hora.get(h, 0) for h in range(24)],
            'by_weekday': [por_semana.get(d, 0) for d in range(7)],   # 0 = lunes
        },
    })
