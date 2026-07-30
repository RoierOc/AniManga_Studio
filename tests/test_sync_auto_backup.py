"""La copia semanal automática decide sola: hay que fijar CUÁNDO guarda y cuándo no.

Un respaldo que hay que acordarse de pulsar no es un respaldo, pero uno automático que se
equivoca es peor: si guardara en cada latido llenaría el repo de commits vacíos, y si no
guardara nunca daría una falsa sensación de estar a salvo. El reloj sale del propio historial
de git (`last_saved_at`), no de un segundo fichero que pueda desincronizarse.

Correr:  .venv/bin/python -m pytest tests/test_sync_auto_backup.py -q
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import api.sync as S  # noqa: E402


@pytest.fixture()
def entorno(monkeypatch):
    """Repo y token configurados, automático encendido, save() instrumentado."""
    hechos = []
    monkeypatch.setattr(S, '_remote_url', lambda: 'https://github.com/yo/perfil.git')
    monkeypatch.setattr(S, '_pat', lambda: 'token')
    monkeypatch.setattr(S, 'get_prefs', lambda: {'sync_auto': True})
    monkeypatch.setattr(S._backend, 'save', lambda: hechos.append('save') or {'ok': True})
    return hechos


def _con_ultimo_guardado(monkeypatch, hace_segundos):
    monkeypatch.setattr(S._backend, 'status',
                        lambda: {'last_saved_at': int(time.time() - hace_segundos)})


def test_guarda_cuando_ha_pasado_la_semana(entorno, monkeypatch):
    _con_ultimo_guardado(monkeypatch, 8 * 86400)
    assert S._auto_tick() == 'saved'
    assert entorno == ['save']


def test_NO_guarda_si_la_copia_es_reciente(entorno, monkeypatch):
    _con_ultimo_guardado(monkeypatch, 2 * 86400)
    assert S._auto_tick() == 'fresh'
    assert entorno == [], 'un commit por latido llenaría el repo de ruido'


def test_sin_repo_o_token_no_es_un_fallo_silencioso_sino_un_estado(entorno, monkeypatch):
    _con_ultimo_guardado(monkeypatch, 30 * 86400)
    monkeypatch.setattr(S, '_pat', lambda: '')
    assert S._auto_tick() == 'unconfigured'   # ≠ 'saved' y ≠ excepción: no hay nada que hacer
    assert entorno == []


def test_apagarla_la_apaga_de_verdad(entorno, monkeypatch):
    _con_ultimo_guardado(monkeypatch, 30 * 86400)
    monkeypatch.setattr(S, 'get_prefs', lambda: {'sync_auto': False})
    assert S._auto_tick() == 'off'
    assert entorno == []


def test_viene_encendida_por_defecto(monkeypatch):
    monkeypatch.setattr(S, 'get_prefs', lambda: {})
    assert S.auto_enabled() is True


def test_un_fallo_de_red_no_mata_el_hilo_y_queda_visible(entorno, monkeypatch):
    """Sin red el push revienta: eso se registra y se reintenta, no se traga."""
    _con_ultimo_guardado(monkeypatch, 8 * 86400)
    monkeypatch.setattr(S._backend, 'save',
                        lambda: (_ for _ in ()).throw(RuntimeError('Push falló: sin red')))
    with pytest.raises(RuntimeError):
        S._auto_tick()          # el bucle lo captura, lo anota en _auto_state y sigue vivo
