"""La detección de carpetas de anime locales: bajar por contenedores sin partir las temporadas.

La biblioteca real del usuario no es plana (`E:/Carpeta Anime/Carpeta Animes/<28 obras>`), así que
mirar un solo nivel devolvía dos carpetas inútiles y ninguna obra. Bajar sin más partiría en
cambio las obras con temporadas en subcarpetas. Estas dos cosas tiran en direcciones opuestas:
son justo lo que hay que fijar.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.anime import _list_anime_folders, _norm_scan_root   # noqa: E402


def _obra(d: Path, n=2):
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (d / f'ep{i:02d}.mkv').write_bytes(b'x')
    return d


def test_baja_por_la_carpeta_contenedora_hasta_las_obras(tmp_path):
    for n in ('Chuunibyou', 'Dororo', 'Hyouka', 'K-ON!!'):
        _obra(tmp_path / 'Carpeta Animes' / n)
    hallado = {Path(p).name for p in _list_anime_folders(str(tmp_path))}
    assert hallado == {'Chuunibyou', 'Dororo', 'Hyouka', 'K-ON!!'}, hallado


def test_una_obra_partida_en_temporadas_sigue_siendo_UNA(tmp_path):
    _obra(tmp_path / 'Monogatari' / 'Season 1')
    _obra(tmp_path / 'Monogatari' / 'Season 2')
    assert [Path(p).name for p in _list_anime_folders(str(tmp_path))] == ['Monogatari']


def test_una_obra_con_un_episodio_por_carpeta_no_se_parte_en_doce(tmp_path):
    # Caso real (`/mnt/d/konejeje`): estructuralmente idéntico al contenedor de arriba — sólo los
    # NOMBRES dicen que son episodios de lo mismo.
    for i in range(1, 13):
        _obra(tmp_path / 'konejeje' / f'[SphinxAnime] K0s2 - {i:02d}', n=1)
    assert [Path(p).name for p in _list_anime_folders(str(tmp_path))] == ['konejeje']


def test_las_carpetas_sin_video_no_ensucian_la_lista(tmp_path):
    (tmp_path / '$RECYCLE.BIN').mkdir()
    (tmp_path / 'Disco de musica').mkdir()
    (tmp_path / 'Disco de musica' / 'a.mp3').write_bytes(b'x')
    _obra(tmp_path / 'Dr.Stone')
    assert [Path(p).name for p in _list_anime_folders(str(tmp_path))] == ['Dr.Stone']


def test_una_raiz_que_no_existe_no_revienta(tmp_path):
    assert _list_anime_folders(str(tmp_path / 'disco desenchufado')) == []


def test_la_forma_guardada_es_unica(tmp_path):
    # `E:\Foo` y `/mnt/e/Foo` no pueden ser dos raíces distintas: los mappings van en forma WSL.
    assert _norm_scan_root('/mnt/e/Carpeta Anime/') == '/mnt/e/Carpeta Anime'
    assert _norm_scan_root('  "/mnt/e/Carpeta Anime"  ') == '/mnt/e/Carpeta Anime'
