"""La detección de páginas a color se memoiza en disco (index_db), no en RAM.

Abrir y decodificar cada imagen costaba **53,7 s en el manga entero** (Amayo, 1861 páginas) EN CADA
apertura del selector. Ahora: 51 s la primera vez, **49 ms** después.

Decisiones que estos tests fijan:
  - Por PÁGINA, no por carpeta: así el filtro por capítulos sigue valiendo (medir sólo lo marcado)
    en vez de forzar a medir el manga completo la primera vez.
  - En DISCO (SQLite): la RAM de esta máquina es limitada y esto es un memo puro.
  - Invalida por mtime del archivo: si la página cambia (p.ej. al traducirla), se vuelve a mirar.

Correr:  python -m pytest tests/test_color_cache.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """index_db aislado: NUNCA el index.db real del usuario."""
    import api.index_db as I
    monkeypatch.setattr(I, '_DB_PATH', str(tmp_path / 'idx.db'))
    monkeypatch.setattr(I, '_conn', None)
    yield I
    if I._conn:
        I._conn.close()
    monkeypatch.setattr(I, '_conn', None)


@pytest.fixture()
def counting(monkeypatch):
    """Cuenta cuántas veces se DECODIFICA una imagen (lo caro)."""
    import api.upscale as U
    calls = {'n': 0}

    def fake(path):
        calls['n'] += 1
        return 0, 0, {'c': 'color' in os.path.basename(path)}

    monkeypatch.setattr(U, '_measure_page_color', fake)
    return calls


def _pages(tmp_path):
    out = []
    for n in ('ch0001_001_color.jpg', 'ch0001_002.jpg', 'ch0002_001_color.jpg'):
        p = tmp_path / n
        p.write_bytes(b'\0' * 64)
        out.append(p)
    return out


def test_detects_and_returns_only_color(db, counting, tmp_path):
    from api.upscale import _detect_color
    got = _detect_color(_pages(tmp_path))
    assert [p.name for p in got] == ['ch0001_001_color.jpg', 'ch0002_001_color.jpg']
    assert counting['n'] == 3


def test_second_call_does_not_decode_again(db, counting, tmp_path):
    """EL PUNTO: 53,7 s → 49 ms. Si esto falla, el selector vuelve a abrir cada imagen."""
    from api.upscale import _detect_color
    pages = _pages(tmp_path)
    first = _detect_color(pages)
    n_after_first = counting['n']
    second = _detect_color(pages)
    assert counting['n'] == n_after_first, 'se volvió a decodificar: el caché no está sirviendo'
    assert [p.name for p in second] == [p.name for p in first]


def test_only_measures_the_pages_you_ask_for(db, counting, tmp_path):
    """Por página, no por carpeta: pedir un capítulo no puede costar medir el manga entero."""
    from api.upscale import _detect_color
    pages = _pages(tmp_path)
    _detect_color(pages[:1])
    assert counting['n'] == 1


def test_changed_page_is_re_measured(db, counting, tmp_path):
    """Traducir reescribe la página: el caché no puede quedarse con el veredicto viejo."""
    import time
    from api.upscale import _detect_color
    pages = _pages(tmp_path)
    _detect_color(pages)
    n = counting['n']
    time.sleep(0.02)                      # el mtime tiene granularidad de tick (~ms)
    pages[1].write_bytes(b'\1' * 128)
    _detect_color(pages)
    assert counting['n'] == n + 1, 'sólo debe re-medirse la página que cambió'


def test_cache_lives_on_disk_not_in_ram(db, counting, tmp_path):
    """Un proceso nuevo (conexión nueva) debe aprovechar lo ya medido: es SQLite en disco."""
    from api.upscale import _detect_color
    pages = _pages(tmp_path)
    _detect_color(pages)
    n = counting['n']
    db._conn.close()
    db._conn = None                        # simula reinicio del server
    _detect_color(pages)
    assert counting['n'] == n, 'se remidió tras "reiniciar": el caché no persiste en disco'


def test_unreadable_page_is_cached_as_not_color(db, tmp_path, monkeypatch):
    """Una página ilegible no puede reintentarse en cada apertura del selector."""
    import api.upscale as U
    calls = {'n': 0}
    real = U._measure_page_color               # capturar ANTES de parchear (si no, se llama a sí mismo)

    def counted(path):
        calls['n'] += 1
        return real(path)                      # el real: la imagen es basura → excepción → False

    monkeypatch.setattr(U, '_measure_page_color', counted)
    p = tmp_path / 'ch0001_001.jpg'
    p.write_bytes(b'no soy una imagen')
    assert U._detect_color([p]) == []
    assert U._detect_color([p]) == []
    assert calls['n'] == 1
