"""Una obra = UNA entrada en la biblioteca de anime.

La costura que falló en producción: una entrada nacida de Jikan queda clavada por `mal_id`,
el backfill le rellena el `al_id` sin reclavarla, y el siguiente alta desde AniList crea un
CLON con sólo los episodios nuevos. Falla en silencio: la app enseña dos tarjetas.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.anime import _lib_key, _canonizar_lib, _carpetas_de, _anadir_carpeta, _escanear_carpetas


def test_reconoce_la_entrada_por_el_otro_id():
    lib = {'61930': {'al_id': 195240, 'mal_id': 61930, 'episodes': {'1': {}}}}
    # Alta desde AniList: trae al_id; la entrada está clavada por mal_id.
    assert _lib_key(lib, al_id=195240, mal_id=None) == '61930'
    assert _lib_key(lib, al_id=None, mal_id=61930) == '61930'
    # Del cliente los ids llegan como texto.
    assert _lib_key(lib, al_id='195240', mal_id=None) == '61930'
    # Otra obra sí acuña clave nueva.
    assert _lib_key(lib, al_id=169580, mal_id=None) == '169580'
    assert _lib_key({}, al_id=None, mal_id=None, title='Uma Musume!') == 'uma_musume_'


def test_canonizar_funde_el_clon_sin_perder_estado():
    lib = {
        '61930':  {'al_id': 195240, 'mal_id': 61930, 'episodes': {'1': {'info_hash': 'a'}},
                   'watched': {'1': True}, 'added_at': 100, 'last_ep': 1},
        '195240': {'al_id': 195240, 'mal_id': 61930, 'local_path': '/mnt/d/Uma',
                   'episodes': {'5': {'info_hash': 'b'}}, 'added_at': 900, 'last_ep': 5},
    }
    assert _canonizar_lib(lib) is True
    assert list(lib) == ['195240']                     # clavada por al_id
    e = lib['195240']
    assert e['local_path'] == '/mnt/d/Uma'             # manda la que tiene carpeta
    assert sorted(e['episodes']) == ['1', '5']         # no se pierde ningún episodio
    assert e['watched'] == {'1': True}                 # ni el progreso del clon
    assert e['added_at'] == 100 and e['last_ep'] == 5


def test_canonizar_no_toca_una_biblioteca_sana():
    lib = {'169580': {'al_id': 169580, 'mal_id': 56734, 'episodes': {}}}
    assert _canonizar_lib(lib) is False
    assert list(lib) == ['169580']


def test_una_serie_repartida_entre_dos_discos(tmp_path):
    """El otro camino del mismo síntoma: la serie está entera, pero en dos carpetas."""
    viejos, nuevos = tmp_path / 'C', tmp_path / 'D'
    for carpeta, nums in ((viejos, (1, 2)), (nuevos, (3, 4))):
        carpeta.mkdir()
        for n in nums:
            (carpeta / f'[Grupo] Serie - {n:02d} [1080p].mkv').write_bytes(b'x')

    anime = {'local_path': str(nuevos)}
    assert _anadir_carpeta(anime, str(viejos)) is True
    assert _anadir_carpeta(anime, str(viejos)) is False        # no se repite
    assert _carpetas_de(anime) == [str(nuevos), str(viejos)]   # la principal, primero

    nums = sorted(e['num'] for e in _escanear_carpetas(anime))
    assert nums == [1, 2, 3, 4], f'faltan episodios de una de las carpetas: {nums}'


def test_sin_carpetas_no_escanea_nada():
    assert _carpetas_de({}) == [] and _escanear_carpetas({}) == []


def _carpeta(tmp_path, nombres):
    for n in nombres:
        (tmp_path / n).write_bytes(b'x')
    return str(tmp_path)


def test_pedir_un_episodio_que_no_esta_no_devuelve_otro(tmp_path):
    """«No está» no puede devolver lo mismo que «aquí lo tienes» (se coló al escalar el ep 99)."""
    from api.anime import _find_video
    d = _carpeta(tmp_path, [f'[G] Serie - {n:02d} [1080p].mkv' for n in (1, 2, 3)])
    assert _find_video(d, 2).endswith('- 02 [1080p].mkv')
    assert _find_video(d, 99) == ''          # antes devolvía el episodio 1


def test_un_solo_fichero_sigue_valiendo_para_cualquier_episodio(tmp_path):
    """Una película o un torrent de un solo vídeo: el número del nombre da igual."""
    from api.anime import _find_video
    d = _carpeta(tmp_path, ['Pelicula Sin Numero.mkv'])
    assert _find_video(d, 1).endswith('Pelicula Sin Numero.mkv')


def test_partes_en_numeral_romano_van_por_posicion(tmp_path):
    """Kizumonogatari I/II/III: ningún nombre lleva número, el orden alfabético ES el orden."""
    from api.anime import _find_video
    d = _carpeta(tmp_path, ['[T] Kizumonogatari I - Tekketsu.mkv',
                            '[T] Kizumonogatari II - Nekketsu.mkv',
                            '[T] Kizumonogatari III - Reiketsu.mkv'])
    assert _find_video(d, 2).endswith('II - Nekketsu.mkv')
    assert _find_video(d, 4) == ''
