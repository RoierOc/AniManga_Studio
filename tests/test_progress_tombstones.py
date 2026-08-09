"""Fusión de progreso con DOS dispositivos: que un borrado no resucite.

La regla vieja («`read` se une») es correcta con un solo cliente y una trampa con dos: desmarcas
un capítulo en el móvil, el PC todavía lo tiene marcado, y la siguiente fusión lo devuelve. Es el
fallo que hace que la gente deje de fiarse del sync, así que va con test antes de que exista el
segundo dispositivo — no después.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.reader import es_lapida, merge_progress, podar_lapidas, sin_lapidas

# Relativos a AHORA a propósito: con marcas fijas del pasado, la poda de 90 días se llevaría las
# lápidas antes de que el test pudiera mirarlas — y el fallo parecería de la fusión.
import time

HOY = int(time.time() * 1000)
AYER = HOY - 24 * 3600 * 1000


def test_capitulo_desmarcado_no_vuelve_al_sincronizar():
    movil = {'Obra': {'read': {}, 'del': {'12': HOY}, 'ts': HOY}}
    pc = {'Obra': {'read': {'11': True, '12': True}, 'ts': AYER}}

    out = merge_progress(pc, movil)

    assert out['Obra']['read'] == {'11': True}, 'el 12 estaba desmarcado y ha vuelto'


def test_da_igual_el_orden_de_la_fusion():
    movil = {'Obra': {'read': {}, 'del': {'12': HOY}, 'ts': HOY}}
    pc = {'Obra': {'read': {'11': True, '12': True}, 'ts': AYER}}

    assert merge_progress(pc, movil)['Obra']['read'] == merge_progress(movil, pc)['Obra']['read']


def test_volver_a_leerlo_despues_gana_a_la_lapida():
    # Lo desmarcaste el lunes y el martes lo leíste otra vez: manda lo último que hiciste.
    borrado = {'Obra': {'read': {}, 'del': {'12': AYER}, 'ts': AYER}}
    releido = {'Obra': {'read': {'12': True}, 'ts': HOY}}

    out = merge_progress(borrado, releido)

    assert out['Obra']['read'] == {'12': True}
    assert 'del' not in out['Obra'], 'la lápida ya no pinta nada y debería irse'


def test_obra_borrada_no_reaparece():
    movil = {'Obra': {'deleted_ts': HOY}}
    pc = {'Obra': {'read': {'1': True}, 'ts': AYER}}

    out = merge_progress(pc, movil)

    assert es_lapida(out['Obra'])
    assert sin_lapidas(out) == {}, 'una obra borrada no se le enseña al cliente'


def test_obra_borrada_revive_si_seguiste_leyendo_en_el_otro_lado():
    borrada = {'Obra': {'deleted_ts': AYER}}
    leyendo = {'Obra': {'read': {'5': True}, 'ts': HOY, 'lastChapter': '5'}}

    out = merge_progress(borrada, leyendo)

    assert not es_lapida(out['Obra'])
    assert out['Obra']['read'] == {'5': True}
    assert out['Obra']['lastChapter'] == '5'


def test_lo_de_siempre_sigue_igual():
    # Sin lápidas de por medio, la fusión no cambia: unión de leídos y posición del ts más nuevo.
    a = {'Obra': {'read': {'1': True}, 'ts': AYER, 'lastChapter': '1', 'lastPage': 3}}
    b = {'Obra': {'read': {'2': True}, 'ts': HOY, 'lastChapter': '2', 'lastPage': 7}}

    out = merge_progress(a, b)

    assert out['Obra']['read'] == {'1': True, '2': True}
    assert out['Obra']['lastChapter'] == '2' and out['Obra']['lastPage'] == 7


def test_las_lapidas_caducan_y_no_engordan_el_fichero():
    viejisimo = HOY - 200 * 24 * 3600 * 1000
    data = {
        'Vieja': {'deleted_ts': viejisimo},
        'Nueva': {'deleted_ts': HOY},
        'Con caps': {'read': {'1': True}, 'del': {'9': viejisimo, '10': HOY}, 'ts': HOY},
    }

    out = podar_lapidas(data, ahora_ms=HOY)

    assert 'Vieja' not in out
    assert 'Nueva' in out
    assert out['Con caps']['del'] == {'10': HOY}


def test_las_lapidas_solo_salen_por_http_si_se_piden(tmp_path, monkeypatch):
    """Un borrado tiene que poder VIAJAR del PC al móvil.

    `sin_lapidas` existe para que la interfaz no pinte obras fantasma, pero aplicado también al
    cliente que sincroniza deja la obra borrada simplemente AUSENTE de la respuesta — y «ausente»
    es lo mismo que dice una obra que nunca existió en el PC. El móvil no puede distinguirlas, se
    queda la obra para siempre y encima la resucita al enviar. Es «falló ≠ no había» aplicado al
    borrado, así que el cliente de sync pide `?lapidas=1` y recibe el mapa crudo.
    """
    from flask import Flask
    from api import reader

    monkeypatch.setattr(reader, '_progress_path', lambda: tmp_path / 'p.json')
    monkeypatch.setattr(reader, '_progress_read',
                        lambda: {'Viva': {'read': {'1': True}, 'ts': HOY},
                                 'Borrada': {'deleted_ts': HOY}})

    app = Flask(__name__)
    app.register_blueprint(reader.reader_bp, url_prefix='/api/reader')
    c = app.test_client()

    normal = c.get('/api/reader/progress').get_json()
    assert 'Borrada' not in normal, 'la interfaz no debe ver obras fantasma'

    sync = c.get('/api/reader/progress?lapidas=1').get_json()
    assert sync['Borrada'] == {'deleted_ts': HOY}, 'el que sincroniza necesita el borrado explícito'
