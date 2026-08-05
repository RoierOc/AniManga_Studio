"""La retrospectiva NO puede contar los marcados en masa como visionados.

MEDIDO sobre el historial real (2026-07-31), hueco entre registros consecutivos de la misma serie:
433 por debajo de 60 s, mediana **5 segundos**. Un episodio dura ~24 min, así que casi todas las
500 entradas son marcados masivos. Un resumen que dijera «has visto 500 episodios en 21 días»
sería falso de forma evidente — la peor manera posible de estrenar una función.
"""
import time

from api.retrospective import GAP_REAL, _clasificar, _racha


def ver(sid, ep, ts):
    return {'anime_id': sid, 'episode': ep, 'watched_at': ts, 'title': f'S{sid}', 'cover': ''}


def test_marcar_una_temporada_entera_no_son_visionados():
    """EL CASO REAL: 12 episodios registrados en 12 segundos."""
    base = 1800000000
    entradas = [ver('a', i, base + i) for i in range(1, 13)]
    reales, marcados = _clasificar(entradas)
    assert len(reales) == 1, 'sólo la primera cuenta; el resto es el marcado'
    assert marcados == 11


def test_ver_episodios_de_verdad_cuentan_todos():
    base = 1800000000
    entradas = [ver('a', i, base + i * 1500) for i in range(1, 6)]   # 25 min entre uno y otro
    reales, marcados = _clasificar(entradas)
    assert len(reales) == 5
    assert marcados == 0


def test_series_distintas_no_se_estorban():
    """Dos series a la vez no son un marcado en masa: el umbral es POR serie."""
    base = 1800000000
    entradas = [ver('a', 1, base), ver('b', 1, base + 5), ver('c', 1, base + 10)]
    reales, marcados = _clasificar(entradas)
    assert len(reales) == 3
    assert marcados == 0


def test_el_umbral_es_el_declarado():
    base = 1800000000
    justo_debajo = _clasificar([ver('a', 1, base), ver('a', 2, base + GAP_REAL - 1)])
    justo_encima = _clasificar([ver('a', 1, base), ver('a', 2, base + GAP_REAL)])
    assert len(justo_debajo[0]) == 1 and justo_debajo[1] == 1
    assert len(justo_encima[0]) == 2 and justo_encima[1] == 0


def test_el_orden_de_entrada_no_altera_el_resultado():
    """`read_all` devuelve de más nuevo a más viejo; la clasificación ordena por su cuenta."""
    base = 1800000000
    entradas = [ver('a', i, base + i * 1500) for i in range(1, 6)]
    assert _clasificar(list(reversed(entradas))) == _clasificar(entradas)


def test_historial_vacio_no_revienta():
    assert _clasificar([]) == ([], 0)


def test_racha_de_dias_seguidos():
    assert _racha({'2026-07-01', '2026-07-02', '2026-07-03', '2026-07-09'}) == (3, '2026-07-03')
    assert _racha({'2026-07-01', '2026-07-05'}) == (1, '2026-07-01')
    assert _racha(set()) == (0, '')


def test_la_racha_cruza_el_cambio_de_mes():
    """Un mes no corta una racha: julio 31 → agosto 1 son días seguidos."""
    assert _racha({'2026-07-30', '2026-07-31', '2026-08-01'}) == (3, '2026-08-01')


def test_la_racha_cruza_el_cambio_de_anio():
    assert _racha({'2025-12-31', '2026-01-01'}) == (2, '2026-01-01')


def test_entrada_sin_fecha_no_rompe_la_clasificacion():
    reales, marcados = _clasificar([{'anime_id': 'a', 'episode': 1}, ver('a', 2, int(time.time()))])
    assert len(reales) + marcados == 2
