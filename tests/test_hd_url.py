"""Resolución máxima en el arte (regla del usuario, 2026-07-31).

Los dos CDN codifican el tamaño EN LA RUTA, así que una URL ya guardada se puede subir sin
volver a preguntar a la API. Importa porque las entradas de biblioteca guardan la URL formada:
lo que se cacheó cuando pedíamos `w1280` se quedaría en w1280 para siempre.
"""
from api.imgproxy import hd_url


def test_tmdb_hero_va_a_original():
    """El hero se pinta a SANGRE: en un monitor de 2560, w1280 se estira al doble."""
    assert hd_url('https://image.tmdb.org/t/p/w1280/a.jpg', hero=True) == \
        'https://image.tmdb.org/t/p/original/a.jpg'
    assert hd_url('https://image.tmdb.org/t/p/w780/a.jpg', hero=True) == \
        'https://image.tmdb.org/t/p/original/a.jpg'


def test_tmdb_sin_hero_sube_un_escalon():
    """Para una tarjeta no hace falta el original: son megas decodificándose al hacer scroll."""
    assert hd_url('https://image.tmdb.org/t/p/w500/a.jpg') == 'https://image.tmdb.org/t/p/w780/a.jpg'
    assert hd_url('https://image.tmdb.org/t/p/w780/a.jpg') == 'https://image.tmdb.org/t/p/w1280/a.jpg'


def test_tmdb_original_se_queda_igual():
    u = 'https://image.tmdb.org/t/p/original/a.jpg'
    assert hd_url(u, hero=True) == u


def test_anilist_sube_la_ruta():
    """EL CASO REAL: a Frieren le falta `extraLarge`, así que `_cover` caía a 230 px."""
    assert hd_url('https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1-a.jpg') == \
        'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1-a.jpg'
    assert hd_url('https://s4.anilist.co/file/anilistcdn/media/anime/cover/small/bx1-a.jpg') == \
        'https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1-a.jpg'


def test_no_toca_lo_que_no_conoce():
    """Un host desconocido o una ruta local se devuelven tal cual, nunca reescritos."""
    for u in ['', None, '/api/library/thumb/Obra', 'https://uploads.mangadex.org/covers/x/y.jpg',
              'https://ejemplo.com/t/p/w780/a.jpg']:
        assert hd_url(u) == u
