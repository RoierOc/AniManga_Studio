"""Aplanar las siete familias de tareas a una sola forma (lo que consume el móvil).

Lo que se prueba es lo que no se ve fallar: que una familia que reporta en **páginas** y otra que
reporta en **tanto por ciento** no acaben pintando la misma barra en sitios distintos, y sobre todo
que un estado desconocido cuente como VIVO. Si contara como terminado, una tarea de horas
desaparecería de la pantalla del móvil mientras sigue corriendo — sin error y sin log.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.status import _aplanar, _map_status  # noqa: E402


def test_las_paginas_y_los_porcentajes_no_se_confunden():
    # Descarga: 5 de 20 PÁGINAS.
    d = _aplanar('downloads', 't1', {'status': 'downloading', 'title': 'Amayo', 'chapter': '7',
                                     'progress': 5, 'total': 20})
    assert d['pct'] == 25 and d['detalle'] == 'Cap. 7'

    # Horneado Anime4K: `progress` YA es 0-100. Tratarlo como "5 de 20" daría 25 % siempre.
    a = _aplanar('anime_upscale', 't2', {'status': 'running', 'title': 'Serie', 'progress': 5})
    assert a['pct'] == 5


def test_un_estado_desconocido_sigue_viva():
    assert _map_status('reticulando_splines') == 'running'
    assert _map_status('no_chapters') == 'error'      # un resultado, no un "sigue corriendo"
    assert _map_status('nothing_to_repair') == 'done'


def test_terminada_es_100_por_ciento():
    t = _aplanar('upscale', 't3', {'status': 'complete', 'title': 'X', 'progress': 87, 'total': 100})
    assert t['estado'] == 'done' and t['pct'] == 100


def test_los_subtitulos_de_un_lote_no_se_cuentan_dos_veces():
    assert _aplanar('subtitles', 't4', {'status': 'running', 'batch_id': 'L1', 'progress': 40}) is None
    assert _aplanar('subtitles', 't5', {'status': 'running', 'progress': 40})['pct'] == 40


def test_una_familia_que_no_conocemos_no_revienta():
    assert _aplanar('inventada', 't6', {'status': 'running'}) is None
    assert _aplanar('downloads', 't7', 'esto no es un dict') is None
    # Sin total no se puede calcular nada: 0, no una división por cero.
    assert _aplanar('downloads', 't8', {'status': 'downloading', 'progress': 3})['pct'] == 0
