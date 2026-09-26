"""El fotograma de episodio: ni churros ni fantasmas.

Dos costuras que fallaban EN SILENCIO y para siempre, porque este caché no caduca nunca:

1. Extraer de un vídeo a medio descargar da un fotograma embadurnado (macrobloques arrastrados)
   que se guarda como si fuera bueno. Visto de verdad: MARRIAGETOXIN ep. 2.
2. La miniatura sobrevivía al vídeo borrado — `has_thumb` sale de un glob de la carpeta de caché,
   no del disco de vídeo—, así que el churro no había forma de quitarlo.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _hay_ffmpeg() -> bool:
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
        return True
    except Exception:
        return False


def test_un_video_truncado_no_deja_fotograma(tmp_path):
    """Lo importante no es que falle: es que NO deje fichero. Un jpg a medias en el caché es
    exactamente el churro inmortal que se estaba arreglando."""
    if not _hay_ffmpeg():
        pytest.skip('ffmpeg no disponible')
    from api import anime

    # `mandelbrot`, no `testsrc`: la carta de ajuste es una rejilla de rectángulos y el detector de
    # embadurnado la puntúa 13,5 (el vídeo real va de 1,2 a 2,5). Mandelbrot puntúa 1,39.
    entero = tmp_path / 'entero.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'mandelbrot=size=640x360:rate=24', '-t', '8',
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(entero)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
    )
    assert entero.exists() and entero.stat().st_size > 0

    bueno = tmp_path / 'bueno.jpg'
    anime._extraer_fotograma(str(entero), bueno, 4.0)      # no debe levantar
    assert bueno.stat().st_size > 0
    assert anime._juzgar_fotograma(bueno) == 'ok'

    # La mitad de los bytes = lo que hay en disco mientras qBittorrent descarga.
    medias = tmp_path / 'medias.mp4'
    medias.write_bytes(entero.read_bytes()[: entero.stat().st_size // 2])
    malo = tmp_path / 'malo.jpg'
    with pytest.raises(Exception):
        anime._extraer_fotograma(str(medias), malo, 4.0)
    assert not malo.exists(), 'un fotograma sucio no puede quedarse en el caché'


def test_borrar_el_video_borra_su_fotograma(tmp_path, monkeypatch):
    from api import anime
    monkeypatch.setattr(anime, '_THUMBS_DIR', tmp_path)

    for n in ('199547_1_w960.jpg', '199547_2_w960.jpg', '199547_2_w480.jpg',
              '199547_sp2_w960.jpg', '199548_2_w960.jpg'):
        (tmp_path / n).write_bytes(b'x')

    # Un episodio: se lleva sus anchos viejos y su gemelo especial, no toca a los vecinos.
    assert anime._borrar_thumbs('199547', 2) == 3
    quedan = {f.name for f in tmp_path.glob('*.jpg')}
    assert quedan == {'199547_1_w960.jpg', '199548_2_w960.jpg'}

    # La serie entera: sólo la suya.
    assert anime._borrar_thumbs('199547') == 1
    assert {f.name for f in tmp_path.glob('*.jpg')} == {'199548_2_w960.jpg'}


def test_un_corte_a_negro_no_se_queda_de_miniatura(tmp_path):
    """El minuto central cae a menudo en el eyecatch o un fundido. Medido sobre el caché real:
    27 de 1099 fotogramas eran negro o blanco entero, y ahí se quedaban para siempre."""
    if not _hay_ffmpeg():
        pytest.skip('ffmpeg no disponible')
    from api import anime

    # 8 s: negro de 3 a 5 (el centro), imagen el resto. El punto medio cae en el negro.
    video = tmp_path / 'con_corte.mp4'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'mandelbrot=size=640x360:rate=24', '-t', '8',
         '-vf', "drawbox=x=0:y=0:w=640:h=360:color=black:t=fill:enable='between(t,3,5)'",
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
    )
    assert video.exists() and video.stat().st_size > 0

    centro = tmp_path / 'centro.jpg'
    anime._extraer_fotograma(str(video), centro, 4.0)          # crudo: cae en el negro
    assert anime._es_plano(centro), 'el montaje de prueba no tiene el corte donde debe'

    util = tmp_path / 'util.jpg'
    anime._extraer_fotograma_util(str(video), util, 8.0)       # con reintento: se sale del negro
    assert not anime._es_plano(util)


def test_un_fotograma_embadurnado_se_reconoce(tmp_path):
    """El juicio va sobre la IMAGEN, no sobre si ffmpeg se quejó: hay series con errores de
    decodificación esporádicos cuyos fotogramas salen perfectos (Welcome to the Ballroom da 2-3
    en TODOS sus episodios). Rechazar por el proceso las dejaba sin una sola miniatura."""
    import numpy as np
    from PIL import Image
    from api import anime

    rnd = np.random.default_rng(7)
    limpio = rnd.integers(0, 255, (540, 960), dtype=np.uint8)
    p_ok = tmp_path / 'limpio.png'
    Image.fromarray(limpio).save(p_ok)
    assert anime._juzgar_fotograma(p_ok) == 'ok'

    # Macrobloques de 8: cada bloque, un color plano → filos fortísimos en la rejilla y nada dentro.
    bloques = rnd.integers(0, 255, (540 // 8, 960 // 8), dtype=np.uint8)
    sucio = np.repeat(np.repeat(bloques, 8, axis=0), 8, axis=1)
    p_mal = tmp_path / 'sucio.png'
    Image.fromarray(sucio).save(p_mal)
    assert anime._juzgar_fotograma(p_mal) == 'sucio'

    # El relleno de h264: medio fotograma de verde. Ni es plano (hay ruido encima) ni tiene filos
    # de macrobloque, y son DOS verdes distintos, así que sólo se pilla por el tono.
    verde = np.zeros((540, 960, 3), dtype=np.uint8)
    verde[:, :480] = (0, 200, 0)
    verde[:, 480:] = (30, 255, 30)
    verde[:150, :400] = rnd.integers(0, 255, (150, 400, 3), dtype=np.uint8)
    p_verde = tmp_path / 'verde.png'
    Image.fromarray(verde).save(p_verde)
    assert anime._juzgar_fotograma(p_verde) == 'sucio'

    # …y un cartel amarillo legítimo (el de Monogatari, 80 % del fotograma saturado) NO puede caer
    # aquí: por saturación se colaba entre la basura, por tono no.
    amarillo = np.zeros((540, 960, 3), dtype=np.uint8)
    amarillo[:, :] = (230, 225, 16)
    amarillo[200:340, 100:860] = 255      # el texto blanco encima
    p_amarillo = tmp_path / 'amarillo.png'
    Image.fromarray(amarillo).save(p_amarillo)
    assert anime._juzgar_fotograma(p_amarillo) == 'ok'


def test_el_still_de_tmdb_no_viene_a_300px(monkeypatch):
    """La tarjeta lo pinta a ~742 px físicos: w300 se estira ×2,5 y se ve blando."""
    from api import anime
    from types import SimpleNamespace

    monkeypatch.setattr(anime, '_tmdb_key', lambda: 'test-key')
    monkeypatch.setattr(anime._http, 'get', lambda *_args, **_kwargs: SimpleNamespace(json=lambda: {
        'episodes': [{
            'episode_number': 1, 'name': 'Capítulo', 'overview': '',
            'still_path': '/still.jpg', 'air_date': '',
        }]
    }))

    result = anime._tmdb_season_episodes(42, 2, 'es-ES')

    assert result['1']['still'] == 'https://image.tmdb.org/t/p/original/still.jpg'
