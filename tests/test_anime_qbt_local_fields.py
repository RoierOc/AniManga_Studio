"""El contrato local no se pierde en la rama de qBittorrent."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class _Reply:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _app():
    from flask import Flask
    from api import anime

    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app


def test_torrent_completado_expone_ruta_local_y_reutiliza_un_escaneo(monkeypatch, tmp_path):
    from api import anime

    content = tmp_path / 'content'
    content.mkdir()
    (content / 'Serie - 01.mkv').write_bytes(b'video-1')
    (content / 'Serie - 01.a4k.mkv').write_bytes(b'video-1-a4k')
    (content / 'Serie - 02.mkv').write_bytes(b'video-2')
    missing_save_path = tmp_path / 'otro-destino'
    calls = []

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return _Reply([{
                'hash': 'HASH-A',
                'progress': 1.0,
                'state': 'stalledUP',
                'size': 123,
                'content_path': str(content),
                'save_path': str(missing_save_path),
            }])
        if path == '/torrents/files':
            return _Reply([
                {'name': 'Serie - 01.mkv'},
                {'name': 'Serie - 02.mkv'},
            ])
        raise AssertionError(f'petición qBittorrent inesperada: {path}')

    original_scan = anime._scan_local_episodes

    def tracked_scan(*args, **kwargs):
        calls.append(args[0])
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(anime, '_backfill_done', True)
    monkeypatch.setattr(anime, '_q', fake_q)
    monkeypatch.setattr(anime, '_video_duration', lambda _path: 1400)
    monkeypatch.setattr(anime, '_scan_local_episodes', tracked_scan)
    monkeypatch.setattr(anime, '_lib_read', lambda: {
        '1': {
            'title': 'Serie',
            'total_episodes': 2,
            'episodes': {
                '0': {'info_hash': 'hash-a', 'from_batch': True},
            },
            'watched': {},
            'positions': {},
            'durations': {},
        },
    })
    anime._qbt_files_cache.clear()
    anime._scan_cache.clear()
    anime._folder_checked_at.clear()

    response = _app().test_client().get('/api/anime/library')

    assert response.status_code == 200
    episodes = response.get_json()[0]['episodes']
    ep1 = next(ep for ep in episodes if ep['num'] == 1)
    ep2 = next(ep for ep in episodes if ep['num'] == 2)
    assert ep1['in_qbt'] is True and ep1['in_local'] is True
    assert ep1['a4k'] is True
    assert ep1['local_path'].endswith('Serie - 01.a4k.mkv')
    assert ep1['original_path'].endswith('Serie - 01.mkv')
    assert ep1['filename'] == 'Serie - 01.mkv'
    assert ep2['in_qbt'] is True and ep2['in_local'] is True
    assert ep2['local_path'].endswith('Serie - 02.mkv')
    assert calls == [str(content)]


def test_torrent_de_un_solo_fichero_casa_el_episodio_1(monkeypatch, tmp_path):
    from api import anime

    video = tmp_path / 'Pelicula.mkv'
    video.write_bytes(b'video')

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return _Reply([{
                'hash': 'HASH-SINGLE',
                'progress': 1.0,
                'state': 'stoppedUP',
                'content_path': str(video),
                'save_path': str(tmp_path / 'otro-destino'),
            }])
        if path == '/torrents/files':
            return _Reply([{'name': video.name}])
        raise AssertionError(f'petición qBittorrent inesperada: {path}')

    monkeypatch.setattr(anime, '_backfill_done', True)
    monkeypatch.setattr(anime, '_q', fake_q)
    monkeypatch.setattr(anime, '_lib_read', lambda: {
        'movie': {
            'title': 'Película',
            'format': 'MOVIE',
            'total_episodes': 1,
            'episodes': {'-1': {'info_hash': 'hash-single'}},
            'watched': {},
            'positions': {},
            'durations': {},
        },
    })
    anime._qbt_files_cache.clear()
    anime._scan_cache.clear()
    anime._folder_checked_at.clear()

    response = _app().test_client().get('/api/anime/library')

    assert response.status_code == 200
    episode = response.get_json()[0]['episodes'][0]
    assert episode['num'] == 1
    assert episode['in_qbt'] is True and episode['in_local'] is True
    assert episode['local_path'].endswith('Pelicula.mkv')
