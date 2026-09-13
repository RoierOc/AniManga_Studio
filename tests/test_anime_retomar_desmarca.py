"""Retomar un episodio ya visto y dejarlo a medias tiene que DESMARCARLO.

El fallo real: se dejaba un episodio a la mitad, la barra salía a la mitad, y al recargar la app
decía «visto». Los tres caminos que escriben progreso guardaban la posición pero no tocaban el
`watched` anterior, así que en el disco quedaban las dos cosas a la vez — medido: 14 series de la
biblioteca real, BOCCHI ep 1 con 853 s de 1420 y «visto».

Es una costura de fallo SILENCIOSO: sólo se nota al recargar, porque el parche optimista de la
interfaz sí desmarcaba.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.anime import _guardar_posicion


def test_retomar_a_la_mitad_desmarca():
    e = {'watched': {'1': True}, 'positions': {}}
    _guardar_posicion(e, '1', 853)
    assert e['positions']['1'] == 853
    assert '1' not in e['watched'], 'un episodio a medias no puede seguir estando visto'


def test_asomarse_un_minuto_no_desmarca():
    """Dos minutos es el umbral: por debajo, volver a abrirlo no borra que lo terminaste."""
    e = {'watched': {'2': True}, 'positions': {}}
    _guardar_posicion(e, '2', 20)
    assert e['positions'] == {}, 'por debajo de 30 s ni siquiera se guarda posición'
    assert e['watched']['2'] is True

    # 33 s es el caso REAL que había en el disco: sí se guarda para reanudar, pero no desmarca.
    _guardar_posicion(e, '2', 33)
    assert e['positions']['2'] == 33
    assert e['watched']['2'] is True

    _guardar_posicion(e, '2', 90)
    assert e['positions']['2'] == 90
    assert e['watched']['2'] is True, 'minuto y medio es asomarse, no volver a verlo'


def test_terminarlo_borra_la_posicion():
    e = {'watched': {}, 'positions': {'3': 800}}
    _guardar_posicion(e, '3', 0)     # visto → el llamador pasa save_pos = 0
    assert '3' not in e['positions']


def test_no_revienta_sin_los_mapas():
    e = {}
    _guardar_posicion(e, '1', 500)
    assert e['positions']['1'] == 500
    _guardar_posicion({}, '1', 0)
