"""El anime se numera de corrido (siempre S01), así que la temporada iba clavada a 1 en la
búsqueda de subtítulos. Para una serie occidental eso pide S01E05 cuando quieres S03E05: baja
un subtítulo REAL, de otro episodio, y se inyecta sin quejarse — desincronizado y con diálogo
que no es el que suena. El fallo es MUDO, por eso se fija aquí.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _stub_ost(monkeypatch):
    """Deja sólo OpenSubtitles en pie y devuelve la lista de URLs que se piden."""
    from api import subtitle
    vistos = []

    monkeypatch.setattr(subtitle, 'get_secret',
                        lambda k, d=None: 'fake-key' if 'API_KEY' in k else d)
    monkeypatch.setattr(subtitle, '_opensubtitles_login', lambda: '')
    monkeypatch.setattr(subtitle, '_ext_search_subdl', lambda *a, **k: [])
    monkeypatch.setattr(subtitle, '_ext_search_subdivx', lambda *a, **k: [])

    class _Resp:
        def read(self): return b'{"data": []}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _urlopen(req, timeout=0):
        vistos.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(subtitle._ur, 'urlopen', _urlopen)
    return vistos


def test_la_busqueda_usa_la_temporada_pedida(monkeypatch):
    from api import subtitle
    vistos = _stub_ost(monkeypatch)
    subtitle._ext_find_spanish_subs(['The Chosen'], episode=5, season=3)
    assert any('season_number=3' in u for u in vistos), vistos


def test_sin_temporada_el_anime_se_comporta_igual_que_antes(monkeypatch):
    from api import subtitle
    vistos = _stub_ost(monkeypatch)
    subtitle._ext_find_spanish_subs(['Bleach'], episode=5)
    assert any('season_number=1' in u for u in vistos), vistos
