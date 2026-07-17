"""Un barrido de cobertura que FALLÓ no puede hacerse pasar por "sólo hay estas fuentes".

Este es el fallo silencioso más caro del proyecto ("falló ≠ no había") apareciendo en la caché de
cobertura. MEDIDO en vivo: Amayo no Tsuki pasó de **54 fuentes (10 ES)** a **34 (2 ES)** porque
Suwayomi se murió a mitad del barrido — y el resultado mutilado se persistió con TTL de 30 días,
pisando el bueno. La UI (Cobertura/Versiones) lo servía como definitivo, y cualquier medición de
"qué fuente española usar" partía ya contaminada: imposible distinguir "esa fuente no sirve" de
"no llegué a verla".

Lo que se comprueba aquí es la EQUIVALENCIA que importa, no un número concreto:
  · un catálogo NO CONSULTABLE devuelve None (≠ {} = "la fuente no tiene capítulos") y no se cachea;
  · un barrido con cualquier problema NO se persiste, así se conserva el barrido bueno anterior;
  · un barrido limpio sí se persiste.

Correr:  .venv/bin/python -m pytest tests/test_coverage_degraded.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture()
def T(monkeypatch):
    """transplant.py con la caché en RAM. NUNCA la caché real del usuario."""
    import api.transplant as T

    store = {}
    monkeypatch.setattr(T, 'cache_set',
                        lambda ns, k, v, ttl=0, max_entries=200: store.__setitem__((ns, k), v))
    monkeypatch.setattr(T, 'cache_get', lambda ns, k, ttl: store.get((ns, k)))
    T._test_store = store
    return T


# ── _source_catalog: None (no consultable) ≠ {} (vacía) ───────────────────────
def test_catalog_unreachable_returns_none_and_is_not_cached(T, monkeypatch):
    """Si no se puede preguntar a la fuente: None, y NADA en la caché.

    Antes devolvía {} y lo cacheaba: la fuente quedaba marcada 'sin capítulos' durante todo el TTL
    aunque tuviera 29. Ese fue el caso real de Mangas.in."""
    def boom(*a, **kw):
        raise RuntimeError("Cloudflare bypass currently disabled")
    # monkeypatch, NUNCA `T._chapters_map = boom`: una asignación directa sobrevive al test y
    # contamina los siguientes (aquí hizo fallar a dos que estaban bien).
    monkeypatch.setattr(T, '_chapters_map', boom)

    cand = {"sourceId": "123", "mangaId": 999, "sourceLang": "es"}
    assert T._source_catalog(cand, "Cualquiera") is None
    assert not [k for k in T._test_store if k[0] == 'coverage'], \
        "un fallo de red NO puede quedar cacheado como catálogo vacío"


def test_catalog_genuinely_empty_is_cached(T, monkeypatch):
    """Una fuente que responde y no tiene capítulos SÍ es un dato: {} y se cachea.

    Es la otra mitad de la equivalencia — sin esto, 'no cachear fallos' degeneraría en
    'no cachear nunca' y el barrido volvería a costar minutos."""
    monkeypatch.setattr(T, '_chapters_map', lambda *a, **kw: {})

    cand = {"sourceId": "123", "mangaId": 999, "sourceLang": "es"}
    assert T._source_catalog(cand, "Cualquiera") == {}
    assert [k for k in T._test_store if k[0] == 'coverage'], \
        "un catálogo vacío REAL sí es información y debe cachearse"


# ── _chapters_map: strict propaga, por defecto no ─────────────────────────────
def test_chapters_map_strict_raises_default_swallows(T, monkeypatch):
    """`strict=True` propaga el fallo; sin él sigue devolviendo {} (13 llamantes dependen de eso)."""
    def boom(*a, **kw):
        raise RuntimeError("caida")
    monkeypatch.setattr(T, '_gql_fast', boom)
    monkeypatch.setattr(T, '_gql', boom)

    assert T._chapters_map(1, timeout=8) == {}
    with pytest.raises(Exception):
        T._chapters_map(1, timeout=8, strict=True)


# ── descubrimiento: un barrido PARCIAL no puede cachearse como completo ───────
def test_search_source_unreachable_is_none_not_empty(T, monkeypatch):
    """Fuente que no responde -> None; fuente que responde sin resultados -> [].

    Sin esta distinción, `_discover_candidates` no puede contar cuántas fuentes se quedaron sin
    mirar, y un barrido a medias se guarda como 'estas son las fuentes de este manga'."""
    class Resp:
        status_code = 500
        def json(self): return {}
    monkeypatch.setattr(T.http_requests, 'post', lambda *a, **kw: Resp())
    assert T._search_source({"id": "1", "name": "X"}, "q") is None

    class Ok:
        status_code = 200
        def json(self): return {"data": {"fetchSourceManga": {"mangas": []}}}
    monkeypatch.setattr(T.http_requests, 'post', lambda *a, **kw: Ok())
    assert T._search_source({"id": "1", "name": "X"}, "q") == []


def test_partial_discovery_is_not_cached(T, monkeypatch):
    """Si alguna búsqueda falló, el barrido NO se persiste (aunque haya encontrado cosas)."""
    monkeypatch.setattr(T, '_search_key', lambda *a: 'k')

    def disco(variants, source_ids=None, on_progress=None, stats=None):
        if stats is not None:
            stats.update({"sources": 2, "tasks": 2, "failed": 1, "listing_error": None})
        return [{"sourceId": "1", "id": 9, "title": "X", "match": 1.0}]
    monkeypatch.setattr(T, '_discover_candidates', disco)

    out = T._candidates_cached('T', None, ['T'], None, refresh=True)
    assert out, "lo encontrado se devuelve igual: el usuario ve lo que hay ahora"
    assert not [k for k in T._test_store if k[0] == 'candidates'], \
        "un barrido con búsquedas fallidas NO puede quedar cacheado como la lista real"


def test_complete_discovery_is_cached(T, monkeypatch):
    """La otra mitad: un barrido limpio SÍ se cachea (si no, cada apertura re-barre 185 fuentes)."""
    monkeypatch.setattr(T, '_search_key', lambda *a: 'k')

    def disco(variants, source_ids=None, on_progress=None, stats=None):
        if stats is not None:
            stats.update({"sources": 2, "tasks": 2, "failed": 0, "listing_error": None})
        return [{"sourceId": "1", "id": 9, "title": "X", "match": 1.0}]
    monkeypatch.setattr(T, '_discover_candidates', disco)

    T._candidates_cached('T', None, ['T'], None, refresh=True)
    assert [k for k in T._test_store if k[0] == 'candidates'], \
        "un barrido completo debe cachearse — si no, 'no cachear parciales' mata el rendimiento"


def test_chapters_map_survives_refresh_failure(T, monkeypatch):
    """El REFRESCO (q1) es best-effort: si falla, la lectura LOCAL (q2) tiene que ocurrir igual.

    Éste es el bug original: q1 y q2 compartían try, y un Cloudflare en q1 tiraba los 29 capítulos
    que q2 habría devuelto desde la BD local."""
    calls = []

    def gql(q, v=None, timeout=None):
        calls.append(q)
        if 'fetchChapters' in q:                 # q1: el refresco desde la web
            raise RuntimeError("Cloudflare bypass currently disabled")
        return {"chapters": {"nodes": [                 # q2: la BD local, intacta
            {"id": 7, "chapterNumber": 1, "pageCount": 20}]}}

    monkeypatch.setattr(T, '_gql_fast', gql)
    monkeypatch.setattr(T, 'normalize_chapter', lambda n: f"ch{int(float(n)):04d}")

    out = T._chapters_map(1, timeout=8)
    assert out, "un fallo del refresco no puede llevarse por delante lo ya sincronizado en local"
    assert len(calls) == 2, "q2 debe ejecutarse aunque q1 reviente"
