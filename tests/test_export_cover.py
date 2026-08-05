"""La portada del tomo llega como data URL desde el navegador.

`FileReader.readAsDataURL` (subir portada, o usar una página a color) manda
`data:image/jpeg;base64,…`. Casi todos los caracteres de ese prefijo ESTÁN en el alfabeto base64,
así que `b64decode` no falla: devuelve bytes corridos, el magic no casa, la reencodificación
revienta y el `except` se comía la portada **sin decir nada** — el CBZ salía sin `000_cover`.

Correr:  .venv/bin/python -m pytest tests/test_export_cover.py -q
"""
import base64
import io
import os
import sys
import zipfile

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _jpeg(color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new('RGB', (40, 60), color).save(buf, 'JPEG')
    return buf.getvalue()


@pytest.fixture()
def manga(tmp_path, monkeypatch):
    """Una página en disco. Aquí sólo se prueba la PORTADA, así que la recolección de páginas
    (que pasa por `roots.series_pages` y la biblioteca real) se sustituye por esa única página."""
    import api.export as E
    page = tmp_path / 'ch0001_001.jpg'
    page.write_bytes(_jpeg((10, 10, 10)))
    monkeypatch.setattr(E, '_collect_images', lambda *a, **k: [('ch0001_001.jpg', page)])
    return E


def _cover_bytes(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.startswith('000_cover')]
        return z.read(names[0]) if names else None


def test_data_url_cover_llega_al_cbz(manga):
    raw = _jpeg()
    data_url = 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()
    path, name = manga.build_archive({
        'title': 'Obra', 'chapters': ['1'], 'volume_name': 'Obra - Tomo 1',
        'cover_data': data_url,
    })
    got = _cover_bytes(path)
    assert got is not None, 'la portada se perdió en silencio'
    # Se reencoda a q=95, así que no se comparan bytes: se comprueba que es la imagen ENVIADA
    # (rojo) y no basura ni la primera página (negra).
    px = Image.open(io.BytesIO(got)).convert('RGB').getpixel((20, 30))
    assert px[0] > 150 and px[1] < 90, f'la portada no es la que se mandó: {px}'
    assert name == 'Obra - Tomo 1.cbz'


def test_base64_pelado_sigue_valiendo(manga):
    """El camino que ya funcionaba (/api/mangadex/cover_b64 devuelve base64 sin prefijo)."""
    path, _ = manga.build_archive({
        'title': 'Obra', 'chapters': ['1'], 'volume_name': 'T',
        'cover_data': base64.b64encode(_jpeg()).decode(),
    })
    assert _cover_bytes(path) is not None


# ── ComicInfo.xml ────────────────────────────────────────────────────────────
# Sin él, un CBZ entra en Komga/Kavita/Mihon como «un archivo llamado X»: sin serie, sin número
# de tomo y ordenado alfabéticamente (Tomo 10 antes que Tomo 2).

def _comicinfo(zip_path):
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(zip_path) as z:
        if 'ComicInfo.xml' not in z.namelist():
            return None
        root = ET.fromstring(z.read('ComicInfo.xml'))
        return {e.tag: e.text for e in root}


def test_comicinfo_lleva_serie_y_numero_de_tomo(manga):
    path, _ = manga.build_archive({
        'title': 'Witch Hat Atelier', 'chapters': ['1', '2'],
        'volume_name': 'Witch Hat Atelier - Tomo 12',
        'meta': {'authors': ['Kamome Shirahama'], 'genres': ['Fantasy'], 'year': '2016',
                 'summary': 'Coco quiere ser bruja.', 'count': 16},
    })
    ci = _comicinfo(path)
    assert ci is not None, 'el CBZ salió sin ComicInfo.xml'
    assert ci['Series'] == 'Witch Hat Atelier'
    assert ci['Number'] == '12' and ci['Volume'] == '12', 'el número de tomo ordena la estantería'
    assert ci['Writer'] == 'Kamome Shirahama'
    assert ci['Count'] == '16'
    assert ci['PageCount'] == '1'


def test_comicinfo_no_escribe_campos_vacios(manga):
    """Un `<Writer></Writer>` vacío es peor que ausente: el lector deja de buscarlo por otro lado."""
    path, _ = manga.build_archive({
        'title': 'Obra', 'chapters': ['1'], 'volume_name': 'Obra - Tomo 1',
    })
    ci = _comicinfo(path)
    assert 'Writer' not in ci and 'Summary' not in ci and 'Year' not in ci
    assert ci['Series'] == 'Obra' and ci['Number'] == '1'


def test_portada_webp_no_se_guarda_como_jpg(manga):
    """La versión anterior asumía «si no es PNG es JPEG» → una portada WebP habría quedado como
    `000_cover.jpg` con bytes WebP dentro: un fichero que miente sobre lo que es."""
    buf = io.BytesIO()
    Image.new('RGB', (40, 60), (0, 120, 200)).save(buf, 'WEBP')
    path, _ = manga.build_archive({
        'title': 'Obra', 'chapters': ['1'], 'volume_name': 'T',
        'cover_data': base64.b64encode(buf.getvalue()).decode(),
    })
    with zipfile.ZipFile(path) as z:
        cov = [n for n in z.namelist() if n.startswith('000_cover')]
        assert cov == ['000_cover.webp'], cov
        assert Image.open(io.BytesIO(z.read(cov[0]))).format == 'WEBP'


def test_portada_jpeg_no_se_reencoda(manga):
    """Reencodar «para normalizar» costaba una generación de pérdida en cada tomo."""
    raw = _jpeg()
    path, _ = manga.build_archive({
        'title': 'Obra', 'chapters': ['1'], 'volume_name': 'T',
        'cover_data': base64.b64encode(raw).decode(),
    })
    assert _cover_bytes(path) == raw, 'la portada se reencodó pudiendo copiarse tal cual'


def test_paginas_en_orden_pese_al_paralelismo(tmp_path, monkeypatch):
    """Las páginas se codifican en varios hilos por TANDAS; el zip tiene que quedar en el mismo
    orden que la lista, o el tomo se lee desordenado. Con 11 páginas y 4 hilos hay 3 tandas, la
    última incompleta: justo donde un `map` mal usado descoloca las cosas."""
    import api.export as E
    paginas = []
    for i in range(1, 12):
        p = tmp_path / f'ch0001_{i:03d}.jpg'
        p.write_bytes(_jpeg((i * 20 % 256, 10, 10)))
        paginas.append((f'Ch0001_{i:03d}.jpg', p))
    monkeypatch.setattr(E, '_collect_images', lambda *a, **k: paginas)

    vistos = []
    tmp, _ = E.build_archive({'title': 'X', 'chapters': ['1'], 'volume_name': 'V'},
                             progress_cb=lambda d, t: vistos.append((d, t)))
    try:
        with zipfile.ZipFile(tmp) as z:
            assert [n for n in z.namelist() if n.startswith('Ch')] == [a for a, _ in paginas]
    finally:
        os.unlink(tmp)
    # El progreso avanza de uno en uno y termina en el total: la barra no puede saltar ni repetir.
    assert vistos == [(i, 11) for i in range(1, 12)]
