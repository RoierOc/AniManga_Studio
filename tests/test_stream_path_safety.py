import pytest
from flask import Flask
from pathlib import Path
from types import SimpleNamespace
from werkzeug.exceptions import NotFound


@pytest.fixture
def stream_api(monkeypatch, tmp_path):
    # La importación limpia sesiones huérfanas; aislarla para no tocar el caché
    # que pudiera estar usando el servidor real.
    monkeypatch.setenv('STREAM_CACHE_DIR', str(tmp_path / 'cache'))
    from api import stream

    sessions = tmp_path / 'sessions'
    sessions.mkdir()
    monkeypatch.setattr(stream, '_SESS_ROOT', sessions)
    app = Flask(__name__)
    app.register_blueprint(stream.stream_bp, url_prefix='/api/stream')
    return stream, app, sessions


def test_subtitle_extraction_uses_video_from_active_session(
        stream_api, monkeypatch, tmp_path):
    stream, app, sessions = stream_api
    sid = 'ab12cd34ef56'
    allowed_video = tmp_path / 'library' / 'episode.mkv'
    attacker_video = tmp_path / 'outside' / 'secret.mkv'
    allowed_video.parent.mkdir()
    attacker_video.parent.mkdir()
    allowed_video.touch()
    attacker_video.touch()
    (sessions / sid / 'subs').mkdir(parents=True)
    (sessions / sid / 'fonts').mkdir()

    seen = []
    commands = []
    monkeypatch.setattr(stream, '_ffprobe',
                        lambda path: seen.append(path) or {'streams': []})

    def fake_run(args, **kwargs):
        commands.append(args)
        Path(args[-1]).touch()
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(stream.subprocess, 'run', fake_run)
    monkeypatch.setattr(stream, '_current', {
        'sid': sid,
        'seek_ctx': {'video': str(allowed_video)},
    })

    response = app.test_client().post(
        '/api/stream/subs', json={'path': str(attacker_video), 'index': 0})

    assert response.status_code == 200
    assert seen == [str(allowed_video)]
    assert commands[0][commands[0].index('-i') + 1] == str(allowed_video)


def test_hls_rejects_non_generated_session_ids(stream_api, tmp_path):
    stream, app, sessions = stream_api
    secret = sessions.parent / 'secret.txt'
    secret.write_text('not an HLS session')

    with app.test_request_context('/api/stream/hls/../secret.txt'):
        response = app.make_response(stream.stream_hls('..', 'secret.txt'))

    assert response.status_code == 404


def test_hls_filename_cannot_escape_session_directory(stream_api):
    stream, app, sessions = stream_api
    sid = 'ab12cd34ef56'
    (sessions / sid).mkdir()
    (sessions.parent / 'secret.txt').write_text('not an HLS segment')

    with app.test_request_context(f'/api/stream/hls/{sid}/../secret.txt'):
        with pytest.raises(NotFound):
            stream.stream_hls(sid, '../secret.txt')
