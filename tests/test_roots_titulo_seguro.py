"""Un título del cliente NUNCA puede salirse de la biblioteca.

`series_dir(title)` era `Path(raiz) / title` a pelo, y `download_source_chapter` deja elegir a
quien llama tanto el título como las URLs de las páginas: destino Y contenido. Con la app
publicada en el tailnet eso es escritura de ficheros arbitraria a cambio de un token.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pytest

from api.roots import titulo_en_disco


ESCAPES = [
    '/etc/cron.d/x',                 # absoluto: en pathlib GANA sobre la raíz
    '../../../../tmp/fuera',         # travesía clásica
    '..\\..\\Windows\\System32',     # la misma con barras de Windows
    'C:/Users/Example/algo',           # ruta de Windows entera
    '....//....//fuera',             # travesía que sobrevive a un replace('../','') ingenuo
]


@pytest.mark.parametrize('malicioso', ESCAPES)
def test_no_se_sale_de_la_raiz(tmp_path, malicioso):
    destino = (tmp_path / titulo_en_disco(malicioso)).resolve()
    assert destino.parent == tmp_path.resolve(), f'{malicioso!r} escapó a {destino}'


@pytest.mark.parametrize('bueno', [
    'Sakamoto Days',
    'Re:Zero kara Hajimeru Isekai Seikatsu',   # los dos puntos son legítimos en un título
    'Kaguya-sama wa Kokurasetai',
    '5-toubun no Hanayome',
    'Umamusume: Cinderella Gray',
])
def test_los_titulos_de_verdad_no_se_tocan(bueno):
    """Si la guarda mutila un título real, rompe bibliotecas enteras: la carpeta deja de existir."""
    assert titulo_en_disco(bueno) == bueno


@pytest.mark.parametrize('vacio', ['', '   ', '.', '..', '/', '///', '...'])
def test_lo_que_no_nombra_nada_revienta(vacio):
    """«Ninguna carpeta» no puede colarse como «la carpeta raíz»: eso borraría la biblioteca."""
    with pytest.raises(ValueError):
        titulo_en_disco(vacio)
