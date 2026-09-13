"""Apagar el PC desde el móvil: las dos costuras que fallan en silencio.

1. El censo de «hay trabajo en marcha». Si se equivoca hacia el lado malo, el apagado se lleva
   por delante un horneado de horas sin decir nada.
2. Que nadie apague este equipo sin pasar por la guardia del token.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import api.power as power


def test_un_estado_desconocido_cuenta_como_vivo(monkeypatch):
    """Lo que no está en la lista de terminadas se considera en marcha, no al revés."""
    monkeypatch.setattr('api.status._all_status', lambda: {
        'downloads': {'a': {'status': 'complete', 'title': 'Ya está'}},
        'anime_upscale': {'b': {'status': 'running', 'title': 'Horneando Uma Musume'}},
        'exports': {'c': {'status': 'estado_que_nadie_ha_escrito_aun'}},
        'subtitles': {},
    })
    vivas = power._ocupado()
    assert 'Horneando Uma Musume' in vivas
    assert 'exports: c' in vivas, 'un estado nuevo no puede leerse como terminado'
    assert not any('Ya está' in v for v in vivas)


def test_la_espera_se_acota(monkeypatch):
    assert power._segundos({}) == power._ESPERA_POR_DEFECTO
    assert power._segundos({'segundos': 'diez'}) == power._ESPERA_POR_DEFECTO
    assert power._segundos({'segundos': -5}) == 0
    assert power._segundos({'segundos': 99999}) == power._ESPERA_MAX


def _cliente(monkeypatch, ordenes):
    monkeypatch.setattr(power, '_shutdown', lambda: ['shutdown'])
    monkeypatch.setattr(power, '_correr', lambda args: (ordenes.append(args), (True, ''))[1])
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(power.power_bp, url_prefix='/api/power')
    return app.test_client()


def test_con_trabajo_en_marcha_no_apaga_sin_que_se_lo_pidan(monkeypatch):
    ordenes = []
    c = _cliente(monkeypatch, ordenes)
    monkeypatch.setattr(power, '_ocupado', lambda: ['Horneando Uma Musume'])

    r = c.post('/api/power/shutdown', json={})
    assert r.status_code == 409 and r.get_json()['ocupado'] == ['Horneando Uma Musume']
    assert ordenes == [], 'se ordenó el apagado pese al 409'

    r = c.post('/api/power/shutdown', json={'forzar': True, 'segundos': 5})
    assert r.status_code == 200 and r.get_json()['segundos'] == 5
    assert ordenes[0][:3] == ['/s', '/t', '5']


def test_sin_windows_lo_dice_en_vez_de_fingir(monkeypatch):
    monkeypatch.setattr(power, '_shutdown', lambda: None)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(power.power_bp, url_prefix='/api/power')
    r = app.test_client().post('/api/power/shutdown', json={})
    assert r.status_code == 501


def test_el_apagado_no_esta_abierto_sin_token():
    """`_ABIERTO` es la lista de rutas que la guardia deja pasar sin token. Ésta no puede estar."""
    from api.auth import _ABIERTO
    assert not any(str(p).startswith('/api/power') for p in _ABIERTO)
