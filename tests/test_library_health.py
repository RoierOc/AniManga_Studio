"""El diagnóstico debe distinguir un chequeo fallido de un resultado limpio."""
import os
import sys
from pathlib import Path

from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _client():
    from api.library_health import health_bp
    app = Flask(__name__)
    app.register_blueprint(health_bp, url_prefix='/api/health')
    return app.test_client()


def test_source_check_failure_is_not_reported_as_healthy(monkeypatch):
    import api.library_health as health
    import api.observability as observability
    import api.source_identity as source_identity

    def fail(*args, **kwargs):
        raise OSError('database unavailable')

    monkeypatch.setattr(source_identity, 'verify_source_refs', fail)
    monkeypatch.setattr(health, '_identity_snapshot', lambda: {
        'unresolved': [], 'unchecked': 0, 'total': 0, 'failed': [],
    })
    monkeypatch.setattr(health, '_services_snapshot', lambda: [])
    monkeypatch.setattr(observability, 'error_counts', lambda: {})

    payload = _client().get('/api/health/check').get_json()

    assert payload['ok'] is False
    assert payload['checks']['sources']['ok'] is False
    assert payload['problems'] == 0


def test_unreadable_identity_is_reported_as_incomplete(monkeypatch, tmp_path):
    import api.library_health as health
    import api.observability as observability
    import api.source_identity as source_identity

    series = tmp_path / 'Serie'
    series.mkdir()
    (series / '.identity.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(health, 'series_titles', lambda: {'serie': [series]})
    monkeypatch.setattr(health, 'series_dirs', lambda _name: [series])
    monkeypatch.setattr(health, 'record_error', lambda *args, **kwargs: None)
    original_read_text = Path.read_text

    def fail_identity(path, *args, **kwargs):
        if path.name == '.identity.json':
            raise OSError('permission denied')
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', fail_identity)

    snapshot = health._identity_snapshot()

    assert snapshot['failed'] == ['Serie']
    assert snapshot['unresolved'] == []

    monkeypatch.setattr(source_identity, 'verify_source_refs', lambda **_kwargs: [])
    monkeypatch.setattr(health, '_services_snapshot', lambda: [])
    monkeypatch.setattr(observability, 'error_counts', lambda: {})
    payload = _client().get('/api/health/check').get_json()
    assert payload['ok'] is False
    assert payload['checks']['identity']['ok'] is False
