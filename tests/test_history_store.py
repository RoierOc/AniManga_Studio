"""El historial se ARCHIVA, no se trunca.

El fallo que arregla esto es de los caros y era invisible: `watch_history.json` guardaba
`history[:500]` y el 2026-07-31 el fichero tenía exactamente 500 entradas cubriendo 21 días.
No era un margen de sobra: estaba lleno, y cada episodio nuevo borraba el más antiguo.
"""
import time

import pytest

from api import history_store


@pytest.fixture
def libdir(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, 'manga_dir', lambda: tmp_path)
    return tmp_path


def test_no_pierde_nada_al_pasar_del_tope(libdir):
    """EL FALLO: con el tope viejo, la entrada 1 desaparecía al llegar a la 501."""
    base = int(time.time())
    total = history_store.LIVE_CAP + 50
    for i in range(total):
        history_store.append('watch', {'anime_id': str(i), 'episode': 1, 'watched_at': base + i})

    vivo = history_store.read_live('watch')
    assert len(vivo) <= history_store.LIVE_CAP, 'el fichero caliente debe quedarse pequeño'

    todo = history_store.read_all('watch')
    assert len(todo) == total, 'ninguna entrada puede perderse'
    assert {e['anime_id'] for e in todo} == {str(i) for i in range(total)}


def test_read_all_ordena_de_reciente_a_antiguo(libdir):
    base = int(time.time())
    for i in range(history_store.LIVE_CAP + 10):
        history_store.append('watch', {'anime_id': str(i), 'episode': 1, 'watched_at': base + i})
    ts = [e['watched_at'] for e in history_store.read_all('watch')]
    assert ts == sorted(ts, reverse=True)


def test_archiva_por_mes(libdir):
    """Las entradas viejas caen en el fichero de SU mes, no en un saco único."""
    ene = int(time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1)))
    jun = int(time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1)))
    for i in range(history_store.LIVE_CAP + 40):
        ts = ene if i < 20 else jun
        history_store.append('watch', {'anime_id': str(i), 'episode': 1, 'watched_at': ts + i})

    meses = {f.stem for f in (libdir / 'watch_history_archive').iterdir()}
    assert '2026-01' in meses and '2026-06' in meses


def test_since_no_trae_lo_anterior_al_periodo(libdir):
    viejo = int(time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1)))
    nuevo = int(time.mktime((2026, 7, 15, 12, 0, 0, 0, 0, -1)))
    for i in range(history_store.LIVE_CAP + 40):
        history_store.append('watch', {'anime_id': str(i), 'episode': 1,
                                       'watched_at': (viejo if i < 30 else nuevo) + i})
    corte = int(time.mktime((2026, 7, 1, 0, 0, 0, 0, 0, -1)))
    assert all(e['watched_at'] >= corte for e in history_store.read_all('watch', since=corte))


def test_dedup_de_anime_colapsa_el_latido_pero_no_una_revisita(libdir):
    """El progreso nativo late cada ~5 s: eso es UNA entrada. Verlo dentro de un año, otra."""
    ahora = int(time.time())
    for _ in range(5):
        history_store.append('watch', {'anime_id': 'a', 'episode': 3, 'watched_at': ahora},
                             dedup_keys=('anime_id', 'episode'))
    assert len(history_store.read_all('watch')) == 1

    history_store.append('watch', {'anime_id': 'a', 'episode': 3, 'watched_at': ahora + 90 * 86400},
                         dedup_keys=('anime_id', 'episode'))
    assert len(history_store.read_all('watch')) == 2


def test_dedup_de_lectura_deja_una_sola_entrada_por_capitulo(libdir):
    """El panel de lectura muestra la ÚLTIMA vez que leíste cada capítulo, sin repetidos."""
    ahora = int(time.time())
    history_store.append('reading', {'title': 'X', 'chapter': '33', 'read_at': ahora},
                         dedup_keys=('title', 'chapter'), dedup_window=0, dedup_scan=0)
    # Mismo capítulo un año después y como NÚMERO, no texto: antes se colaba duplicado.
    history_store.append('reading', {'title': 'X', 'chapter': 33, 'read_at': ahora + 365 * 86400},
                         dedup_keys=('title', 'chapter'), dedup_window=0, dedup_scan=0)
    filas = history_store.read_all('reading')
    assert len(filas) == 1
    assert filas[0]['read_at'] == ahora + 365 * 86400


def test_fichero_ausente_no_es_un_fallo(libdir):
    assert history_store.read_live('watch') == []
    assert history_store.read_all('watch') == []


def test_clear_borra_tambien_el_archivo(libdir):
    """«Limpiar» dejaba doce ficheros de historial vivos que reaparecían en la retrospectiva."""
    base = int(time.time())
    for i in range(history_store.LIVE_CAP + 40):
        history_store.append('watch', {'anime_id': str(i), 'episode': 1, 'watched_at': base + i})
    assert history_store.read_all('watch')
    history_store.clear('watch')
    assert history_store.read_all('watch') == []


def test_si_archivar_falla_no_se_recorta(libdir, monkeypatch):
    """«Falló» no puede acabar como «no había»: mejor un fichero grande que perder entradas."""
    base = int(time.time())
    for i in range(history_store.LIVE_CAP - 1):
        history_store.append('watch', {'anime_id': str(i), 'episode': 1, 'watched_at': base + i})

    def boom(*a, **k):
        raise OSError('disco lleno')
    monkeypatch.setattr(history_store, '_archive_month', boom)

    for i in range(10):
        history_store.append('watch', {'anime_id': f'n{i}', 'episode': 1, 'watched_at': base + 9000 + i})
    assert len(history_store.read_live('watch')) == history_store.LIVE_CAP + 9
