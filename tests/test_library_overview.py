"""El cruce disco↔seguimiento: la parte que puede confundir dos obras distintas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.library_overview import build_overview, safe_tracked  # noqa: E402


def _folder(name, **kw):
    return {'id': name, 'name': name, 'chapter_count': 3, 'upscaled': 0, 'cover': None, **kw}


def test_identidad_de_fuente_gana_al_titulo():
    """Dos entradas con el MISMO título: debe elegir la que casa por source_id/manga_id."""
    folders = [_folder('Amayo', source_meta={'sourceId': 7, 'mangaId': 'abc'})]
    tracked = [
        {'id': 'amayo', 'title': 'Amayo', 'kind': 'mangadex', 'status': 'dropped'},
        {'id': 'src_7_abc', 'title': 'Amayo', 'kind': 'source', 'status': 'reading'},
    ]
    out = build_overview(folders, tracked)
    disco = [x for x in out if not x.get('trackedOnly')][0]
    assert disco['trackedId'] == 'src_7_abc'
    assert disco['status'] == 'reading'
    assert disco['mdId'] is None              # kind='source' no tiene id de MangaDex
    # Comportamiento HEREDADO del frontend, fijado aquí a propósito: la homónima que NO casó
    # queda oculta porque `seen` se lleva por título. Cambiarlo es una decisión de producto,
    # no un efecto colateral de mover el código — por eso el test lo afirma en vez de callarlo.
    assert len(out) == 1


def test_titulo_es_el_ultimo_recurso():
    folders = [_folder('Sakamoto Days')]
    tracked = [{'id': 'uuid-1', 'title': 'sakamoto days', 'status': 'reading'}]
    out = build_overview(folders, tracked)
    assert out[0]['trackedId'] == 'uuid-1'
    assert out[0]['mdId'] == 'uuid-1'


def test_seguido_sin_descargar_no_desaparece():
    out = build_overview([], [{'id': 'uuid-2', 'title': 'Vinland Saga', 'cover': 'c.jpg'}])
    assert out[0]['trackedOnly'] is True
    assert out[0]['chapter_count'] == 0
    assert out[0]['cover'] == 'c.jpg'


def test_no_duplica_lo_que_ya_esta_en_disco():
    out = build_overview([_folder('Berserk')], [{'id': 'uuid-3', 'title': 'Berserk'}])
    assert len(out) == 1


def test_carpeta_sin_seguimiento_se_conserva_entera():
    out = build_overview([_folder('Suelto', upscaled=9)], [])
    assert out[0]['upscaled'] == 9 and out[0]['status'] == '' and out[0]['trackedId'] is None


def test_json_roto_no_vacia_la_biblioteca():
    """«Falló» no puede leerse como «no sigues nada»: se registra y las carpetas se sirven igual."""
    def boom():
        raise ValueError('local_library.json corrupto')

    assert safe_tracked(boom) == []
    assert len(build_overview([_folder('Amayo')], safe_tracked(boom))) == 1
