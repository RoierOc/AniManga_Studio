"""La ficha técnica resuelve episodios por identidad, nunca por una ruta del cliente."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def _client():
    from flask import Flask
    from api.anime_media_info import anime_media_info_bp

    app = Flask(__name__)
    app.register_blueprint(anime_media_info_bp, url_prefix='/api/anime/media_info')
    return app.test_client()


@pytest.fixture(autouse=True)
def clear_probe_cache():
    from api import anime_media_info

    anime_media_info._probe_cached.cache_clear()
    yield
    anime_media_info._probe_cached.cache_clear()


def test_devuelve_ficha_de_video_y_pistas_sin_revelar_la_ruta(monkeypatch, tmp_path):
    from api import anime_media_info as media

    video = tmp_path / 'capitulo.mkv'
    video.write_bytes(b'video fixture')
    calls = []
    probe_calls = []

    def resolve(anime_id, episode, episode_key):
        calls.append((anime_id, episode, episode_key))
        return str(video), None

    payload = {
        'format': {'format_name': 'matroska,webm', 'duration': '1422.5'},
        'streams': [
            {'codec_type': 'video', 'codec_name': 'hevc', 'width': 1920, 'height': 1080},
            {'codec_type': 'audio', 'codec_name': 'opus', 'channels': 2,
             'tags': {'language': 'jpn', 'title': 'Japanese'}},
            {'codec_type': 'subtitle', 'codec_name': 'ass',
             'tags': {'language': 'spa', 'title': 'Español'}},
        ],
    }
    monkeypatch.setattr(media, 'video_de_biblioteca', resolve)

    def probe(*args, **kwargs):
        probe_calls.append(args[0])
        return SimpleNamespace(stdout=json.dumps(payload))

    monkeypatch.setattr(media.subprocess, 'run', probe)

    client = _client()
    response = client.get('/api/anime/media_info/42/s02e001')
    repeated = client.get('/api/anime/media_info/42/s02e001')

    assert response.status_code == 200
    assert calls == [('42', 1, 's02e001'), ('42', 1, 's02e001')]
    assert response.json['video'] == {'codec': 'hevc', 'width': 1920, 'height': 1080}
    assert response.json['duration_seconds'] == 1422.5
    assert response.json['audio_tracks'][0]['language'] == 'jpn'
    assert response.json['subtitle_tracks'][0]['title'] == 'Español'
    assert response.json['size_bytes'] == len(b'video fixture')
    assert str(video) not in response.get_data(as_text=True)
    assert repeated.status_code == 200
    assert len(probe_calls) == 1


def test_episodio_ausente_es_404_y_no_se_confunde_con_un_resultado_vacio(monkeypatch):
    from api import anime_media_info as media

    monkeypatch.setattr(media, 'video_de_biblioteca', lambda *_: (None, ('missing', 404)))

    response = _client().get('/api/anime/media_info/42/1')

    assert response.status_code == 404
    assert response.json['error']
    assert 'video' not in response.json


def test_no_acepta_ruta_local_proporcionada_por_query(monkeypatch, tmp_path):
    from api import anime_media_info as media

    video = tmp_path / 'server-resolved.mkv'
    video.write_bytes(b'x')
    calls = []
    monkeypatch.setattr(media, 'video_de_biblioteca', lambda *args: (calls.append(args) or (str(video), None)))
    monkeypatch.setattr(media.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps({
        'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1, 'height': 1}],
    })))

    response = _client().get('/api/anime/media_info/42/1?local_path=%2Fetc%2Fpasswd')

    assert response.status_code == 200
    assert calls == [('42', 1, '1')]
    assert response.json['size_bytes'] == 1


def test_clave_de_episodio_invalida_se_rechaza_antes_de_resolver(monkeypatch):
    from api import anime_media_info as media

    monkeypatch.setattr(media, 'video_de_biblioteca', lambda *_: pytest.fail('no debe resolver'))

    response = _client().get('/api/anime/media_info/42/../../etc')

    assert response.status_code in (400, 404)
