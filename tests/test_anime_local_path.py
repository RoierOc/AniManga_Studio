"""Un anime bajado por torrent debe quedar apuntado a su carpeta en disco (`local_path`).

El fallo que fija: `local_path` sólo se rellenaba al vincular una carpeta A MANO, así que una
serie bajada por torrent existía para la app únicamente mientras el torrent siguiera en
qBittorrent — toda la UI decide con `in_local || (in_qbt && progress>=100)`. Al quitar el torrent
conservando los ficheros, el episodio DESAPARECÍA de la biblioteca aunque el .mkv siguiera en el
disco. Silencioso y con toda la pinta de un borrado de datos.

Correr:  python -m pytest tests/test_anime_local_path.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _save_dir(monkeypatch, title, base):
    import api.anime as A
    monkeypatch.setattr(A, '_anime_settings_read', lambda: {'download_path': base})
    return A._anime_save_dir(title)


def test_save_dir_es_la_misma_para_descargar_y_para_localizar(monkeypatch, tmp_path):
    """La carpeta que se le pasa a qBittorrent y la que la biblioteca busca son LA MISMA.
    Si divergen, la app deja de encontrar ficheros que sí están en el disco."""
    d = _save_dir(monkeypatch, 'Botan Kamiina: Fully Blossoms', str(tmp_path))
    assert d.startswith(str(tmp_path))
    # El ':' no puede sobrevivir: es ilegal en nombres de carpeta de Windows.
    assert ':' not in os.path.basename(d)
    assert d == _save_dir(monkeypatch, 'Botan Kamiina: Fully Blossoms', str(tmp_path))


def test_sin_download_path_no_inventa_carpeta(monkeypatch):
    """Sin carpeta configurada y sin qBittorrent, devuelve '' — nunca una ruta a medias
    que luego se guardaría como un local_path fantasma."""
    import api.anime as A
    monkeypatch.setattr(A, '_anime_settings_read', lambda: {'download_path': ''})
    monkeypatch.setattr(A, '_q', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('qbt caído')))
    assert A._anime_save_dir('Lo Que Sea') == ''


def test_local_path_solo_si_la_carpeta_existe(monkeypatch, tmp_path):
    """Guardar un local_path que no existe sería peor que no guardarlo: la rama local
    ganaría y la serie se mostraría vacía."""
    import api.anime as A
    real = tmp_path / 'Serie Real'
    real.mkdir()
    monkeypatch.setattr(A, '_anime_settings_read', lambda: {'download_path': str(tmp_path)})
    assert os.path.isdir(A._anime_save_dir('Serie Real'))
    assert not os.path.isdir(A._anime_save_dir('Serie Que No Bajé'))
