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


@pytest.mark.parametrize('torrent', [False, True], ids=['carpeta-local', 'torrent'])
def test_respaldo_tmdb_queda_en_disco_y_sobrevive_proceso_nuevo(tmp_path, monkeypatch, torrent):
    import hashlib
    import io
    from flask import Flask
    from PIL import Image
    from api import anime, imgproxy, runtime

    thumbs = tmp_path / 'thumbs'
    proxy = tmp_path / 'proxy'
    proxy.mkdir()
    monkeypatch.setattr(anime, '_THUMBS_DIR', thumbs)
    monkeypatch.setattr(imgproxy, '_CACHE_DIR', proxy)
    monkeypatch.setattr(imgproxy, '_INDEX', {})
    monkeypatch.setattr(runtime, '_CACHE_DIR', tmp_path / 'metadata')
    still = 'https://image.tmdb.org/t/p/original/episode-two.jpg'
    Image.new('RGB', (1280, 720), (20, 40, 200)).save(
        proxy / (hashlib.sha256(still.encode()).hexdigest() + '.jpg'))
    runtime.cache_set('ep_meta', '62640_s1_v5', {'2': {'still': still}})
    entry = {'local_path': str(tmp_path), 'tmdb_id': 62640, 'tmdb_type': 'tv',
             'season_year': 2014, 'episodes': {'2': {'info_hash': 'torrent'}} if torrent else {}}
    monkeypatch.setattr(anime, '_lib_read', lambda: {'18897': entry})
    monkeypatch.setattr(anime, '_buscar_video', lambda *_: 'episode-two.mkv')
    monkeypatch.setattr(anime, 'resolve_episode_video', lambda *_: ('episode-two.mkv', None))
    monkeypatch.setattr(anime, '_ffprobe_duration', lambda *_: 1451)
    monkeypatch.setattr(anime, '_tmdb_key', lambda: 'test-key')
    monkeypatch.setattr(anime, '_episode_meta_season', lambda *_: (1, False))

    def corrupt_frame(*_):
        raise RuntimeError('ningún momento da un fotograma utilizable')

    monkeypatch.setattr(anime, '_extraer_fotograma_util', corrupt_frame)

    def client():
        app = Flask(__name__)
        app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
        return app.test_client()

    first = client().get('/api/anime/thumb/18897/2?episode_key=2')
    assert first.status_code == 200
    saved = thumbs / '18897_2_w960.jpg'
    assert saved.exists()
    with Image.open(io.BytesIO(first.data)) as im:
        assert im.width == 960
        assert im.getpixel((20, 20))[2] > 180

    # Un intérprete nuevo, sin acceso a metadata/vídeo ni estado RAM, recibe el mismo JPEG.
    script = '''
import hashlib, sys
from pathlib import Path
from flask import Flask
from api import anime
anime._THUMBS_DIR = Path(sys.argv[1])
def unavailable():
    raise AssertionError('No se debe volver a consultar la biblioteca')
anime._lib_read = unavailable
app = Flask(__name__)
app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
response = app.test_client().get('/api/anime/thumb/18897/2?episode_key=2')
print(response.status_code, hashlib.sha256(response.data).hexdigest())
'''
    from pathlib import Path
    returned = subprocess.run(
        [sys.executable, '-c', script, str(thumbs)], capture_output=True, text=True, timeout=15,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')},
        check=True,
    )
    assert returned.stdout.strip() == '200 ' + hashlib.sha256(first.data).hexdigest()
    assert saved.read_bytes() == first.data


def test_especial_corrupto_no_hereda_el_still_del_episodio_regular(tmp_path, monkeypatch):
    from flask import Flask
    from api import anime

    monkeypatch.setattr(anime, '_THUMBS_DIR', tmp_path)
    monkeypatch.setattr(anime, '_lib_read', lambda: {'18897': {'local_path': str(tmp_path)}})
    monkeypatch.setattr(anime, '_escanear_carpetas', lambda *_: [
        {'num': 2, 'ep_type': 'special', 'path': 'special-two.mkv'},
    ])
    monkeypatch.setattr(anime, '_ffprobe_duration', lambda *_: 100)
    def corrupt_frame(_video, dest, _duration):
        dest.write_bytes(b'JPEG incompleto')
        raise RuntimeError('fotograma corrupto')
    monkeypatch.setattr(anime, '_extraer_fotograma_util', corrupt_frame)
    def regular_metadata(_id):
        raise AssertionError('Un especial no puede usar metadata de un episodio regular')
    monkeypatch.setattr(anime, '_episode_metadata', regular_metadata)
    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')

    response = app.test_client().get('/api/anime/thumb/18897/2?special=1')

    assert response.status_code == 500
    assert not list(tmp_path.glob('*.jpg'))


def test_pregeneracion_respeta_identidad_de_temporada_de_la_biblioteca(tmp_path, monkeypatch):
    from PIL import Image
    from api import anime

    monkeypatch.setattr(anime, '_THUMBS_DIR', tmp_path)
    monkeypatch.setattr(anime, '_lib_read', lambda: {'42': {'local_path': str(tmp_path)}})
    monkeypatch.setattr(anime.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(anime, '_video_duration', lambda *_: 100)
    monkeypatch.setattr(anime, '_escanear_carpetas', lambda *_: [
        {'num': 2, 'season': 1, 'path': 'season-one.mkv'},
        {'num': 2, 'season': 2, 'path': 'season-two.mkv'},
    ])
    def frame(video, dest, _duration):
        color = (200, 20, 20) if video == 'season-one.mkv' else (20, 20, 200)
        Image.new('RGB', (960, 540), color).save(dest)
    monkeypatch.setattr(anime, '_extraer_fotograma_util', frame)

    anime._pregen_thumbs()

    assert {p.name for p in tmp_path.glob('*.jpg')} == {
        '42_s01e002_w960.jpg', '42_s02e002_w960.jpg',
    }
    with Image.open(tmp_path / '42_s01e002_w960.jpg') as first:
        assert first.getpixel((10, 10))[0] > 180
    with Image.open(tmp_path / '42_s02e002_w960.jpg') as second:
        assert second.getpixel((10, 10))[2] > 180
