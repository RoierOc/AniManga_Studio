"""La versión horneada con Anime4K manda sobre el original — y no se cuela como episodio aparte.

Es el corazón de «si se escala, pasa a ser el capítulo principal». Falla con el código anterior a
`_prefiere_a4k`, que devolvía el original porque ordenaba los ficheros alfabéticamente y
`ep01.a4k.mkv` < `ep01.mkv` era pura casualidad de la tabla ASCII.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.anime import _find_video, a4k_de, original_de   # noqa: E402


def _crea(d: Path, *nombres):
    for n in nombres:
        (d / n).write_bytes(b'x')


def test_horneada_gana_al_original(tmp_path):
    _crea(tmp_path, 'Serie - 01.mkv', 'Serie - 01.a4k.mkv', 'Serie - 02.mkv')
    assert _find_video(str(tmp_path), 1).endswith('Serie - 01.a4k.mkv')
    # El 2 no tiene horneada: sigue siendo el original, no la del 1.
    assert _find_video(str(tmp_path), 2).endswith('Serie - 02.mkv')


def test_sin_horneada_devuelve_el_original(tmp_path):
    _crea(tmp_path, 'Serie - 01.mkv')
    assert _find_video(str(tmp_path), 1).endswith('Serie - 01.mkv')


def test_la_horneada_no_roba_el_numero_de_otro_episodio(tmp_path):
    # Sin excluirlas del emparejamiento, `_parse_episode('Serie - 01.a4k')` da 1 igual que el
    # original y uno de los dos se quedaba fuera o desplazaba la numeración.
    _crea(tmp_path, 'Serie - 01.mkv', 'Serie - 01.a4k.mkv', 'Serie - 03.mkv')
    assert _find_video(str(tmp_path), 3).endswith('Serie - 03.mkv')


def test_ida_y_vuelta_entre_original_y_horneada(tmp_path):
    _crea(tmp_path, 'Serie - 01.mkv', 'Serie - 01.a4k.mkv')
    orig, horn = str(tmp_path / 'Serie - 01.mkv'), str(tmp_path / 'Serie - 01.a4k.mkv')
    assert a4k_de(orig) == horn
    assert a4k_de(horn) == horn            # idempotente: pasarle la horneada no la duplica
    assert original_de(horn) == orig
    assert original_de(orig) == orig


def test_una_horneada_huerfana_no_se_confunde_con_su_original(tmp_path):
    # El original borrado a mano: `original_de` no puede inventarse una ruta que no existe.
    _crea(tmp_path, 'Serie - 01.a4k.mkv')
    assert original_de(str(tmp_path / 'Serie - 01.a4k.mkv')) == ''


def test_fichero_suelto_tambien_prefiere_la_horneada(tmp_path):
    # qBittorrent puede dar la ruta del fichero directamente, no la carpeta.
    _crea(tmp_path, 'Serie - 01.mkv', 'Serie - 01.a4k.mkv')
    assert _find_video(str(tmp_path / 'Serie - 01.mkv'), 1).endswith('.a4k.mkv')


def test_el_listado_no_inventa_un_episodio_fantasma(tmp_path, monkeypatch):
    """`_parse_episode('Serie - 01.a4k')` da -1, así que sin excluirla la horneada caía en la rama
    de «número no reconocido» y se colaba como UN EPISODIO MÁS. Con 12 episodios horneados eso son
    12 fantasmas y la numeración corrida."""
    from api import anime

    _crea(tmp_path, 'Serie - 01.mkv', 'Serie - 01.a4k.mkv', 'Serie - 02.mkv')
    monkeypatch.setattr(anime, '_video_duration', lambda p: 1400.0)   # nada es un «special»
    anime._scan_cache.clear()

    eps = anime._scan_local_episodes_sync(str(tmp_path), {})
    assert [e['num'] for e in eps] == [1, 2]
    uno = next(e for e in eps if e['num'] == 1)
    assert uno['a4k'] is True and uno['path'].endswith('.a4k.mkv')
    assert uno['original_path'].endswith('Serie - 01.mkv')
    assert 'a4k' not in next(e for e in eps if e['num'] == 2)


def test_la_api_no_se_come_la_marca_a4k(tmp_path, monkeypatch):
    """El endpoint arma cada episodio con una lista FIJA de claves: lo que no se nombre ahí no
    llega a la UI. `a4k` se perdía justo así, y con él el distintivo de la tarjeta, el «volver al
    original» y —lo importante— el apagado de shaders al reproducir (aplicarlos sobre un horneado
    es pasar la red dos veces). Además el título salía con `.a4k` pegado, porque se derivaba del
    nombre del fichero horneado."""
    from api import anime

    _crea(tmp_path, 'Serie - 01.mkv', 'Serie - 01.a4k.mkv', 'Serie - 02.mkv')
    monkeypatch.setattr(anime, '_video_duration', lambda p: 1400.0)
    monkeypatch.setattr(anime, '_es_sub_injected', lambda p: False)
    monkeypatch.setattr(anime, '_lib_read', lambda: {
        '1': {'id': '1', 'title': 'Serie', 'local_path': str(tmp_path), 'episodes': {}},
    })
    monkeypatch.setattr(anime, '_qbt_torrents', lambda *a, **k: [], raising=False)
    anime._scan_cache.clear()

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    r = app.test_client().get('/api/anime/library')
    assert r.status_code == 200

    serie = next(a for a in r.get_json() if a['id'] == '1')
    ep1 = next(e for e in serie['episodes'] if e['num'] == 1)
    ep2 = next(e for e in serie['episodes'] if e['num'] == 2)
    assert ep1['a4k'] is True and ep1['local_path'].endswith('.a4k.mkv')
    assert ep1['original_path'].endswith('Serie - 01.mkv')
    assert '.a4k' not in (ep1['title'] or '')
    assert ep2['a4k'] is False
