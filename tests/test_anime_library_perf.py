"""Las dos costuras que hacían que /api/anime/library tardara ~3 s en CADA carga.

Medido sobre la biblioteca real (84 series, 601 episodios locales, discos de Windows vía DrvFS,
donde un stat cuesta ~0,7 ms):

  - `_es_sub_injected` hacía hasta 5 `os.path.exists` POR EPISODIO  → ~3000 stats = 2,1 s
  - el endpoint sumaba `Path(ep['path']).stat().st_size` POR EPISODIO →  599 stats = 0,69 s

Resultado: 2900 ms → 35 ms en las llamadas repetidas (el front las repite cada 15 s mientras
descargas). Estos tests fijan el COMPORTAMIENTO; que sea rápido se midió a mano.

Correr:  python -m pytest tests/test_anime_library_perf.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture(autouse=True)
def _clean():
    import api.anime as A
    A._es_marks.clear()
    A._es_marks_at.clear()
    yield
    A._es_marks.clear()
    A._es_marks_at.clear()


def _vid(tmp_path, name='Ep01.mkv'):
    f = tmp_path / name
    f.write_bytes(b'\0' * 128)
    return f


def test_no_marker_means_not_injected(tmp_path):
    from api.anime import _es_sub_injected
    assert _es_sub_injected(str(_vid(tmp_path))) is False


def test_detects_our_sidecar(tmp_path):
    """Lo que escribe hoy la traducción: <vídeo>.spa.ass junto al vídeo."""
    from api.anime import _es_sub_injected
    v = _vid(tmp_path)
    (tmp_path / 'Ep01.spa.ass').write_text('x')
    assert _es_sub_injected(str(v)) is True


def test_detects_legacy_marker(tmp_path):
    """Episodios inyectados en el MKV antes del cambio a sidecar: sólo tienen el marcador."""
    from api.anime import _es_sub_injected
    v = _vid(tmp_path)
    (tmp_path / 'Ep01.es_injected').write_text('1')
    assert _es_sub_injected(str(v)) is True


@pytest.mark.parametrize('ext', ['.srt', '.ass', '.ssa', '.vtt'])
def test_every_sidecar_extension(tmp_path, ext):
    from api.anime import _es_sub_injected
    v = _vid(tmp_path)
    (tmp_path / f'Ep01.spa{ext}').write_text('x')
    assert _es_sub_injected(str(v)) is True


def test_a_sibling_episodes_sidecar_is_not_ours(tmp_path):
    """El listdir ve TODA la carpeta: hay que casar por nombre base o el episodio 2 heredaría
    el subtítulo del 1 y la biblioteca mentiría sobre qué está traducido."""
    from api.anime import _es_sub_injected
    _vid(tmp_path, 'Ep01.mkv')
    v2 = _vid(tmp_path, 'Ep02.mkv')
    (tmp_path / 'Ep01.spa.ass').write_text('x')
    assert _es_sub_injected(str(v2)) is False


def test_prefix_collision_does_not_leak(tmp_path):
    """'Ep1.mkv' no puede quedarse con el sidecar de 'Ep10.mkv'."""
    from api.anime import _es_sub_injected
    v = _vid(tmp_path, 'Ep1.mkv')
    _vid(tmp_path, 'Ep10.mkv')
    (tmp_path / 'Ep10.spa.ass').write_text('x')
    assert _es_sub_injected(str(v)) is False


def test_cache_notices_a_new_sidecar(tmp_path, monkeypatch):
    """El caché no puede volverse una mentira: al traducir aparece el sidecar y el badge debe
    salir. Se invalida por mtime de la carpeta (aquí sin TTL, que es lo que se mide).

    El `sleep` NO es un parche para que pase: el mtime de un directorio tiene la granularidad del
    reloj del kernel (~ms), así que dos cambios en el MISMO tick colapsan en el mismo valor y el
    caché no puede verlos. Sin la pausa se mediría esa limitación del reloj, no nuestro código.
    Es irrelevante en producción (traducir tarda minutos) y la comparte el `_scan_local_episodes`
    que ya existía. Verificado en tmpfs, ext4 y DrvFS: con separación real, el mtime cambia.
    """
    import time
    import api.anime as A
    monkeypatch.setattr(A, '_FOLDER_MTIME_TTL', 0)
    v = _vid(tmp_path)
    assert A._es_sub_injected(str(v)) is False
    time.sleep(0.02)
    (tmp_path / 'Ep01.spa.ass').write_text('x')
    assert A._es_sub_injected(str(v)) is True


def test_missing_folder_is_false_not_a_crash(tmp_path):
    from api.anime import _es_sub_injected
    assert _es_sub_injected(str(tmp_path / 'no' / 'existe.mkv')) is False


def test_cache_is_bounded(tmp_path, monkeypatch):
    """Cota dura: es un caché de conveniencia. Sin ella, una biblioteca enorme lo haría crecer
    sin freno — y la RAM de esta máquina es limitada."""
    import api.anime as A
    monkeypatch.setattr(A, '_ES_MARKS_MAX', 4)
    for i in range(12):
        d = tmp_path / f'f{i}'
        d.mkdir()
        (d / 'E.mkv').write_bytes(b'\0')
        A._es_sub_injected(str(d / 'E.mkv'))
    assert len(A._es_marks) <= 4
    assert len(A._es_marks_at) <= 4      # el de timestamps también, o crecería solo


def test_scan_reports_size_so_the_endpoint_need_not_stat(tmp_path):
    """El tamaño viaja DENTRO del resultado cacheado del escaneo. Si dejara de venir, el endpoint
    volvería a hacer un stat por episodio (~690 ms por carga) o daría 0 bytes por serie."""
    from api.anime import _scan_local_episodes
    (tmp_path / 'Show - 01.mkv').write_bytes(b'\0' * 4096)
    (tmp_path / 'Show - 02.mkv').write_bytes(b'\0' * 2048)
    eps = _scan_local_episodes(str(tmp_path), {})
    assert eps, 'el escaneo no encontró los episodios'
    assert sum(e.get('size') or 0 for e in eps) == 6144
