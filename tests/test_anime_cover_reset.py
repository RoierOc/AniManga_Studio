"""El reinicio de portada solo cambia la ficha elegida y vuelve a resolver su arte."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def _app():
    from flask import Flask
    from api import anime

    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app


def test_reinicio_resuelve_y_limita_cambios_a_un_anime(monkeypatch, tmp_path):
    from api import anime
    from api import runtime

    lib_path = tmp_path / 'anime_library.json'
    seleccionada = {
        'al_id': 42,
        'title': 'Serie A',
        'title_english': 'Series A',
        'title_romaji': 'Serie A',
        'season_year': 2024,
        'format': 'TV',
        'cover': 'https://image.tmdb.org/t/p/w780/old.jpg',
        'cover_xl': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/old.jpg',
        'cover_source': 'manual',
        'cover_locked': True,
        'tmdb_id': 10,
        '_cover_tried': True,
        '_season_tried': True,
        'banner': 'https://image.tmdb.org/t/p/w780/banner.jpg',
        'episodes': {'1': {'info_hash': 'keep-this'}},
        'watched': {'1': True},
    }
    otra = {'al_id': 99, 'title': 'Serie B', 'cover': 'https://s4.anilist.co/other.jpg'}
    lib_path.write_text(json.dumps({'42': seleccionada, '99': otra}), encoding='utf-8')
    thumbs_dir = tmp_path / 'thumbs'
    thumbs_dir.mkdir()
    selected_thumb = thumbs_dir / '42_1_w960.jpg'
    unrelated_thumb = thumbs_dir / '99_1_w960.jpg'
    selected_thumb.write_bytes(b'old episode frame')
    unrelated_thumb.write_bytes(b'keep other anime')
    monkeypatch.setattr(anime, '_THUMBS_DIR', thumbs_dir)
    monkeypatch.setattr(anime, '_lib_path', lambda: lib_path)
    monkeypatch.setattr(anime, '_anilist_enrich', lambda al_id: {
        'title': {'english': 'Series A', 'romaji': 'Serie A'},
        'seasonYear': 2024,
        'format': 'TV',
        'coverImage': {'extraLarge': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/new.jpg'},
    })
    monkeypatch.setattr(anime, '_tmdb_key', lambda: 'test-key')
    monkeypatch.setattr(anime, '_tmdb_art', lambda *args, **kwargs: {
        'tmdb_id': 11,
        'tmdb_type': 'tv',
        'poster': 'https://image.tmdb.org/t/p/w780/new.jpg',
    })
    episode_stills = {
        '10_s1_v4': {'1': {'still': 'https://image.tmdb.org/t/p/original/old-still.jpg'}},
        '11_s1_v4': {'1': {'still': 'https://image.tmdb.org/t/p/original/new-still.jpg'}},
    }
    invalidated_meta = []
    monkeypatch.setattr(runtime, 'cache_get', lambda namespace, key, ttl: episode_stills.get(key))
    monkeypatch.setattr(runtime, 'cache_invalidate', lambda namespace, key=None: invalidated_meta.append((namespace, key)))
    monkeypatch.setattr(anime, '_tmdb_season_by_year', lambda tmdb_id, year: (1, None))
    invalidated = []
    monkeypatch.setattr(anime, '_invalidate_img', lambda url: invalidated.append(url) or 1)

    response = _app().test_client().post('/api/anime/library/42/reset_cover')

    assert response.status_code == 200
    saved = json.loads(lib_path.read_text(encoding='utf-8'))
    actual = saved['42']
    assert actual['cover'] == 'https://image.tmdb.org/t/p/w780/new.jpg'
    assert actual['cover_source'] == 'tmdb'
    assert actual['cover_locked'] is False
    assert actual['cover_rev'] == 1
    assert not selected_thumb.exists()
    assert unrelated_thumb.exists()
    assert actual['tmdb_id'] == 11
    assert actual['banner'] == seleccionada['banner']
    assert actual['episodes'] == seleccionada['episodes']
    assert actual['watched'] == seleccionada['watched']
    assert saved['99'] == otra
    assert set(invalidated) == {
        seleccionada['cover'], seleccionada['cover_xl'],
        'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/new.jpg',
        'https://image.tmdb.org/t/p/w780/new.jpg',
        'https://image.tmdb.org/t/p/original/old-still.jpg',
        'https://image.tmdb.org/t/p/original/new-still.jpg',
    }
    assert set(invalidated_meta) == {('ep_meta', '10_s1_v4'), ('ep_meta', '11_s1_v4')}


def test_reinicio_de_portada_inexistente_responde_404(monkeypatch, tmp_path):
    from api import anime

    lib_path = tmp_path / 'anime_library.json'
    lib_path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(anime, '_lib_path', lambda: lib_path)

    response = _app().test_client().post('/api/anime/library/404/reset_cover')

    assert response.status_code == 404
