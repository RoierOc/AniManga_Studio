"""La intención descargar → 4K debe sobrevivir a los snapshots de una descarga."""
import sys
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def test_la_intencion_de_cadena_no_se_pierde_al_actualizar_el_estado(monkeypatch):
    from api import download as d

    monkeypatch.setattr(d, 'download_status', {})
    monkeypatch.setattr(d, '_persist_download_status', lambda: None)
    lanzadas = []
    monkeypatch.setattr(d, '_launch_chained_upscale', lambda tid, payload: lanzadas.append((tid, payload.copy())))

    d.set_download_status('t1', {
        'status': 'starting', 'title': 'Obra', 'chapter': '1',
        'chain_upscale': True, 'chain_eco': False,
    })
    d.set_download_status('t1', {
        'status': 'downloading', 'title': 'Obra', 'chapter': '1',
        'progress': 4, 'total': 10,
    })
    d.set_download_status('t1', {
        'status': 'complete', 'title': 'Obra', 'chapter': '1',
        'progress': 10, 'total': 10,
    })

    assert len(lanzadas) == 1
    assert lanzadas[0][0] == 't1'
    assert lanzadas[0][1]['chain_upscale'] is True
    assert lanzadas[0][1]['chain_eco'] is False

    # Un snapshot terminal repetido no vuelve a encolar el mismo capítulo.
    d.set_download_status('t1', {'status': 'complete', 'title': 'Obra', 'chapter': '1'})
    assert len(lanzadas) == 1


def test_la_cadena_invoca_el_mismo_lanzador_de_upscale(monkeypatch):
    from api import download as d

    monkeypatch.setattr(d, 'download_status', {
        't2': {
            'status': 'complete', 'title': 'Obra', 'chapter': '2',
            'chain_eco': True, 'chain_fast': True,
        },
    })
    monkeypatch.setattr(d, '_persist_download_status', lambda: None)
    llamadas = []

    from api import upscale
    monkeypatch.setattr(upscale, 'start_upscale_chapter', lambda payload: (
        llamadas.append(payload) or ({'status': 'started', 'task_id': 'up-2'}, 200)
    ))

    d._launch_chained_upscale('t2', d.download_status['t2'])

    assert llamadas == [{
        'title': 'Obra', 'chapter': '2', 'eco': True, 'fast': True,
    }]
    assert d.download_status['t2']['chain_status'] == 'started'
    assert d.download_status['t2']['chain_task_id'] == 'up-2'


def test_las_cadenas_se_guardan_y_se_eliminan_por_titulo(monkeypatch, tmp_path):
    from api import download as d

    app = Flask(__name__)
    app.register_blueprint(d.download_bp, url_prefix='/api/download')
    monkeypatch.setattr(d, '_CHAIN_FILE', tmp_path / 'download_chains.json')

    client = app.test_client()
    response = client.post('/api/download/chains', json={
        'title': 'Obra', 'chapters': ['1', '1', '2'],
        'waiting': ['2'], 'translatable': ['1'],
        'upscale': True, 'translate': True, 'stage': 'downloading',
    })

    assert response.status_code == 200
    saved = client.get('/api/download/chains').get_json()['Obra']
    assert saved['chapters'] == ['1', '2']
    assert saved['waiting'] == ['2']
    assert saved['translatable'] == ['1']

    assert client.delete('/api/download/chains', json={'title': 'Obra'}).status_code == 200
    assert client.get('/api/download/chains').get_json() == {}
