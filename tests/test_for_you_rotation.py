"""«Para ti» tiene que MOVERSE de un día para otro.

Antes las semillas eran siempre las mismas (las 8 más vistas en anime, las 8 alfabéticamente
primeras en manga), así que el riel enseñaba lo mismo indefinidamente aunque la caché caducase:
se recalculaba idéntico. La rotación es determinista a propósito — el mismo día da siempre el
mismo corte, o cambiaría bajo los pies al recargar y la caché no serviría de nada.
"""
from api.for_you import _MUESTRA, _semillas, rotar


def test_el_mismo_dia_da_siempre_lo_mismo():
    """Determinista: si no, recargar cambiaría el riel y la caché no valdría."""
    c = list(range(20))
    assert rotar(c, 8, '2026-07-31') == rotar(c, 8, '2026-07-31')


def test_dias_distintos_dan_cortes_distintos():
    c = list(range(20))
    cortes = {tuple(rotar(c, 8, f'2026-08-{d:02d}')) for d in range(1, 15)}
    assert len(cortes) > 1, 'catorce días seguidos no pueden dar el mismo corte'


def test_dos_dias_seguidos_no_comparten_semillas():
    """El paso es el tamaño de la ventana justamente para esto: si sólo se desplazara una
    posición, 7 de 8 semillas seguirían siendo las mismas y el riel apenas se movería."""
    c = list(range(24))
    for d in range(1, 20):
        hoy = set(rotar(c, 8, f'2026-08-{d:02d}'))
        mañana = set(rotar(c, 8, f'2026-08-{d + 1:02d}'))
        assert not (hoy & mañana), f'día {d} y {d + 1} comparten semillas'


def test_una_fecha_ilegible_no_revienta():
    c = list(range(20))
    assert len(rotar(c, 8, 'no-es-una-fecha')) == 8


def test_no_pierde_candidatos_si_hay_menos_que_la_muestra():
    """Con pocas series no hay nada que rotar: se devuelven todas, no un trozo."""
    c = [1, 2, 3]
    assert rotar(c, 8, '2026-07-31') == c
    assert rotar([], 8, '2026-07-31') == []


def test_devuelve_exactamente_lo_pedido():
    assert len(rotar(list(range(20)), 8, '2026-07-31')) == 8


def test_todos_los_candidatos_salen_alguna_vez():
    """La rotación no puede dejar a una serie fuera para siempre."""
    c = list(range(20))
    vistos = set()
    for d in range(1, 29):
        vistos |= set(rotar(c, 8, f'2026-09-{d:02d}'))
    assert vistos == set(c)


def _lib(n):
    """n series empezadas, con distinto número de episodios vistos."""
    return {
        str(i): {'al_id': 1000 + i, 'watched': {str(e): True for e in range(1, (i % 9) + 2)},
                 'last_watched_at': 1800000000 - i}
        for i in range(n)
    }


def test_las_semillas_salen_de_lo_MAS_visto():
    """No alfabético: una serie abandonada en el episodio 1 no pesa como una terminada."""
    s = _semillas(_lib(30), '2026-07-31')
    assert len(s) == _MUESTRA
    assert all(isinstance(x, int) for x in s)


def test_una_serie_sin_ver_nada_no_siembra():
    lib = {'a': {'al_id': 1, 'watched': {}}, 'b': {'al_id': 2, 'watched': {'1': True}}}
    assert _semillas(lib, '2026-07-31') == [2]


def test_sin_al_id_no_siembra():
    lib = {'a': {'watched': {'1': True}}}
    assert _semillas(lib, '2026-07-31') == []


def test_las_semillas_cambian_de_dia_en_dia():
    lib = _lib(30)
    cortes = {tuple(_semillas(lib, f'2026-08-{d:02d}')) for d in range(1, 15)}
    assert len(cortes) > 1
