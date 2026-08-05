"""Un solo convenio para el estado de seguimiento.

MEDIDO sobre la biblioteca real (87 series) el 2026-07-31: 22 entradas SIN estado, más un
'FINISHED' y un '' sueltos. `FINISHED` es el estado de EMISIÓN de AniList, no de seguimiento:
se coló al importar. Consecuencia: los pills de filtro mentían, y las 22 sin estado no salían
en ninguno aunque sí en «Todo».
"""
from api.anime import _norm_status


def test_respeta_lo_que_el_usuario_eligio():
    """Un estado explícito NUNCA se deduce por encima, ni aunque contradiga el progreso."""
    assert _norm_status('dropped', vistos=12, total=12) == 'dropped'
    assert _norm_status('on_hold', vistos=0, total=24) == 'on_hold'
    assert _norm_status('plan_to_watch', vistos=5, total=10) == 'plan_to_watch'


def test_traduce_los_convenios_de_anilist():
    """EL FALLO CONCRETO: la serie con 'FINISHED' no aparecía en ningún filtro."""
    assert _norm_status('FINISHED', 0, 0) == 'completed'
    assert _norm_status('CURRENT', 0, 0) == 'watching'
    assert _norm_status('PLANNING', 0, 0) == 'plan_to_watch'
    assert _norm_status('PAUSED', 0, 0) == 'on_hold'


def test_sin_estado_lo_deduce_del_progreso():
    assert _norm_status(None, vistos=12, total=12) == 'completed'
    assert _norm_status('', vistos=13, total=12) == 'completed'   # extras vistos ⇒ sigue terminada
    assert _norm_status('', vistos=4, total=12) == 'watching'
    assert _norm_status('   ', vistos=0, total=12) == 'plan_to_watch'


def test_sin_total_no_se_declara_terminada():
    """Sin total conocido, «vistos >= total» sería cierto con total=0 y marcaría TODO completado."""
    assert _norm_status(None, vistos=3, total=0) == 'watching'
    assert _norm_status(None, vistos=0, total=0) == 'plan_to_watch'


def test_siempre_devuelve_un_estado_valido():
    validos = {'watching', 'completed', 'plan_to_watch', 'on_hold', 'dropped'}
    for raw in [None, '', '  ', 'basura', 'FINISHED', 'watching', 0, 'RELEASING']:
        assert _norm_status(raw, 2, 10) in validos
