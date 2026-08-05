"""El proxy de imágenes no puede aplastar la transparencia.

Los LOGOS de TMDB (el rótulo del título sobre el hero) son PNG con alfa. `_render_variant`
convertía SIEMPRE a RGB y guardaba JPEG, así que pedir una rendición de un logo devolvía las
letras sobre un cuadro NEGRO encima del arte. Por eso los logos se servían sin pasar por el proxy:
a tamaño original y sin caché (1,2 MB medidos para pintarlos en 543 px).

Este fallo es silencioso en el peor sentido: el `<img>` carga, no hay error en consola y sólo se
ve mirando la pantalla. De ahí el test.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

Image = pytest.importorskip("PIL.Image", reason="Pillow no instalado")


def _png_con_alfa(p: Path, w=1600, h=900):
    im = Image.new("RGBA", (w, h), (255, 0, 0, 0))
    im.putpixel((w // 2, h // 2), (255, 255, 255, 255))   # un trazo opaco en medio
    im.save(p)
    return p


def test_una_imagen_con_alfa_conserva_la_transparencia(tmp_path, monkeypatch):
    from api import imgproxy

    monkeypatch.setattr(imgproxy, "_CACHE_DIR", tmp_path)
    src = _png_con_alfa(tmp_path / "logo.png")

    out = imgproxy._render_variant(src, "clave", 320)

    assert out is not None, "la reducción debería producirse (1600 → 320)"
    with Image.open(out) as im:
        assert "A" in im.getbands(), "se perdió el canal alfa: el logo saldría sobre un cuadro"
        assert im.width == 320


def test_una_foto_sin_alfa_sigue_saliendo_en_jpeg(tmp_path, monkeypatch):
    """El formato con alfa es la EXCEPCIÓN. Si se aplicara a todo, cada portada de la biblioteca
    pasaría a WebP y se perdería el ajuste de calidad medido para las páginas con texto."""
    from api import imgproxy

    monkeypatch.setattr(imgproxy, "_CACHE_DIR", tmp_path)
    src = tmp_path / "portada.jpg"
    Image.new("RGB", (800, 1200), (10, 20, 30)).save(src)

    out = imgproxy._render_variant(src, "clave2", 320)

    assert out is not None and out.suffix == ".jpg"


def test_el_acierto_de_cache_encuentra_las_DOS_extensiones(tmp_path, monkeypatch):
    """La rendición con alfa se guarda con otra extensión. Si la comprobación de caché sólo mirase
    `.jpg`, cada visita volvería a decodificar y reescalar el mismo logo."""
    from api import imgproxy

    monkeypatch.setattr(imgproxy, "_CACHE_DIR", tmp_path)
    src = _png_con_alfa(tmp_path / "logo2.png")

    primera = imgproxy._render_variant(src, "clave3", 320)
    assert primera is not None
    # Se borra el ORIGEN: si el segundo intento no fuese un acierto de caché, tendría que fallar.
    src.unlink()
    segunda = imgproxy._render_variant(src, "clave3", 320)

    assert segunda == primera
