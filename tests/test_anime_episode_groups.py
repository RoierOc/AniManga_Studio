"""La metadata de temporadas anime debe respetar el orden local de TMDB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def _app():
    from flask import Flask
    from api import anime

    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app


class _Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _episode_group_api(monkeypatch, *, groups, production, tv=None, regular=None):
    from api import anime, runtime

    monkeypatch.setattr(anime, '_lib_read', lambda: {
        '42': {
            'tmdb_id': 65942,
            'tmdb_type': 'tv',
            'season_year': 2026,
            'title_english': 'Re:Zero 4th Season',
            'title_romaji': 'Re:Zero kara Hajimeru Isekai Seikatsu 4th Season',
        }
    })
    monkeypatch.setattr(anime, '_tmdb_key', lambda: 'test-key')
    monkeypatch.setattr(anime, '_tmdb_season_by_year', lambda *_: (None, None))
    monkeypatch.setattr(runtime, 'cache_get', lambda *_: None)
    monkeypatch.setattr(runtime, 'cache_set', lambda *_args, **_kwargs: None)

    def get(url, *, params, timeout):
        if url.endswith('/episode_groups'):
            return _Response({'results': groups})
        if '/episode_group/' in url:
            group_id = url.rsplit('/', 1)[-1]
            source = tv if group_id == 'tv' else production
            return _Response((source or {}).get(params.get('language'), {}))
        if '/season/' in url:
            return _Response(regular or {})
        raise AssertionError(f'Unexpected TMDB URL: {url}')

    monkeypatch.setattr(anime._http, 'get', get)


def _episode(*, id, order, episode_number, title, still):
    return {
        'id': id,
        'order': order,
        'season_number': 1,
        'episode_number': episode_number,
        'name': title,
        'overview': f'Sinopsis {title}',
        'still_path': still,
        'air_date': '2026-01-01',
    }


def _season(name, episodes):
    return {'id': name.lower().replace(' ', '-'), 'name': name, 'episodes': episodes}


def test_temporada_tmdb_estandar_no_se_desplaza(monkeypatch):
    from api import anime

    monkeypatch.setattr(anime, '_lib_read', lambda: {
        '42': {'tmdb_id': 65942, 'tmdb_type': 'tv', 'season_year': 2020}
    })
    monkeypatch.setattr(anime, '_tmdb_key', lambda: 'test-key')
    monkeypatch.setattr(anime, '_tmdb_season_by_year', lambda *_: (2, None))
    from api import runtime
    monkeypatch.setattr(runtime, 'cache_get', lambda *_: None)
    monkeypatch.setattr(runtime, 'cache_set', lambda *_args, **_kwargs: None)

    def get(url, *, params, timeout):
        assert url.endswith('/season/2')
        return _Response({'episodes': [{
            'episode_number': 1, 'name': 'Episodio correcto',
            'overview': 'Sinopsis', 'still_path': '/correct.jpg', 'air_date': '2020-01-01',
        }]})

    monkeypatch.setattr(anime._http, 'get', get)

    response = _app().test_client().get('/api/anime/episode_meta/42')

    assert response.json['season'] == 2
    assert response.json['meta']['1']['title'] == 'Episodio correcto'
    assert response.json['meta']['1']['still'].endswith('/correct.jpg')


def test_temporada_anime_usa_el_orden_del_grupo_no_el_numero_global(monkeypatch):
    first = _season('Season 1', [_episode(
        id=1001, order=0, episode_number=1, title='No pertenece a T4', still='/s1.jpg')])
    fourth = _season('Season 4', [
        _episode(id=1068, order=0, episode_number=68, title='Inicio de T4', still='/s4e1.jpg'),
        _episode(id=1069, order=1, episode_number=69, title='Segundo de T4', still='/s4e2.jpg'),
    ])
    english = {'groups': [first, fourth]}
    spanish = {'groups': [
        _season('Season 1', [_episode(
            id=1001, order=0, episode_number=1, title='No pertenece a T4', still='/s1.jpg')]),
        _season('Season 4', [
            _episode(id=1068, order=0, episode_number=68, title='Inicio de T4', still='/s4e1.jpg'),
            _episode(id=1069, order=1, episode_number=69, title='Segundo de T4', still='/s4e2.jpg'),
        ]),
    ]}
    _episode_group_api(monkeypatch,
        groups=[{'id': 'production', 'type': 6, 'name': 'Seasons (Production)'}],
        production={'en-US': english, 'es-ES': spanish})

    response = _app().test_client().get('/api/anime/episode_meta/42')

    assert response.json['source'] == 'tmdb'
    assert response.json['season'] == 4
    assert response.json['meta']['1']['title'] == 'Inicio de T4'
    assert response.json['meta']['1']['still'].endswith('/s4e1.jpg')
    assert response.json['meta']['2']['title'] == 'Segundo de T4'


def test_sin_grupo_de_temporada_no_usa_t1_como_respaldo_incorrecto(monkeypatch):
    _episode_group_api(monkeypatch,
        groups=[{'id': 'production', 'type': 6, 'name': 'Seasons (Production)'}],
        production={'en-US': {'groups': [_season('Season 1', [_episode(
            id=1001, order=0, episode_number=1, title='Falso T1', still='/wrong.jpg')])]},
                    'es-ES': {'groups': []}},
        regular={'episodes': [{
            'episode_number': 1, 'name': 'Falso T1', 'overview': '',
            'still_path': '/wrong.jpg', 'air_date': '',
        }]})

    response = _app().test_client().get('/api/anime/episode_meta/42')

    assert response.json == {'source': None, 'meta': {}}


def test_grupos_de_produccion_ambiguos_no_eligen_un_still(monkeypatch):
    fourth = _season('Season 4', [_episode(
        id=1068, order=0, episode_number=68, title='Posible T4', still='/maybe.jpg')])
    detail = {'en-US': {'groups': [fourth]}, 'es-ES': {'groups': [fourth]}}
    _episode_group_api(monkeypatch,
        groups=[
            {'id': 'production', 'type': 6, 'name': 'Seasons (Production)'},
            {'id': 'production-two', 'type': 6, 'name': 'Alternative production order'},
        ],
        production=detail)

    response = _app().test_client().get('/api/anime/episode_meta/42')

    assert response.json == {'source': None, 'meta': {}}
