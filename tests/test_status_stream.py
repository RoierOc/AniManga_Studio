from flask import Flask

import api.status as status


def test_stream_expone_error_de_snapshot_en_vez_de_latido_vacio(monkeypatch):
    def fail_snapshot():
        raise RuntimeError('fallo de snapshot')

    monkeypatch.setattr(status, '_all_status', fail_snapshot)
    app = Flask(__name__)
    app.register_blueprint(status.status_bp, url_prefix='/api/status')
    response = app.test_client().get('/api/status/stream', buffered=False)
    try:
        assert next(response.response) == b'data: {"_error":true}\n\n'
    finally:
        response.close()
