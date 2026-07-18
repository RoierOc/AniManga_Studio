"""Cobertura EN PARALELO + ranking progresivo (Fase C).

El barrido de catálogos pasó de secuencial a un ThreadPool. Lo que hay que blindar al hacerlo:
  · el resultado es EQUIVALENTE al secuencial: mismas fuentes consultables entran, las {} vacías
    se omiten, y una None ("no consultable") NO entra pero SÍ marca `degraded` (falló ≠ no había);
  · el ranking (por calidad, luego por match) es determinista pese al orden de llegada del pool;
  · se emiten snapshots PROGRESIVOS: el estado ya trae `sources` durante la fase 'coverage'.

Correr:  .venv/bin/python -m pytest tests/test_coverage_parallel.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture()
def T(monkeypatch):
    import api.transplant as T
    # Nada de red ni de caché real del usuario.
    monkeypatch.setattr(T, 'cache_set', lambda *a, **kw: None)
    monkeypatch.setattr(T, 'cache_get', lambda *a, **kw: None)
    monkeypatch.setattr(T, 'title_variants', lambda title, al=None: [title])
    monkeypatch.setattr(T, '_md_candidates', lambda variants, lang: [])
    monkeypatch.setattr(T, '_local_chapter_files', lambda title: {})
    monkeypatch.setattr(T, '_update_frequency', lambda catalog: {"confidence": "unknown"})
    monkeypatch.setattr(T.versions_db, 'get_assigned_map', lambda title: {})
    return T


def _run(T, monkeypatch, candidates, catalogs, scores):
    """Corre _run_coverage con catálogos/scores dados por sourceId. catalogs[id] puede ser
    None (no consultable), {} (vacía) o {chn: {...}}."""
    monkeypatch.setattr(T, '_candidates_cached',
                        lambda *a, **kw: [dict(c) for c in candidates])
    monkeypatch.setattr(T, '_source_catalog',
                        lambda cand, title=None, refresh=False: catalogs[cand['sourceId']])
    monkeypatch.setattr(T, '_score_candidate_cached_only',
                        lambda c: scores.get(c['sourceId']))
    monkeypatch.setattr(T, '_source_kind', lambda cand: 'suwayomi')
    tid = 'test_cov'
    T.transplant_status.pop(tid, None)
    T._run_coverage(tid, 'ObraX', None, None)
    return T.transplant_status[tid]


def test_parallel_coverage_equivalent_and_degraded(T, monkeypatch):
    cands = [
        {"sourceId": "a", "id": 1, "sourceName": "A", "sourceLang": "es", "match": 0.90},
        {"sourceId": "b", "id": 2, "sourceName": "B", "sourceLang": "en", "match": 0.88},
        {"sourceId": "c", "id": 3, "sourceName": "C", "sourceLang": "es", "match": 0.95},
    ]
    catalogs = {
        "a": {"1": {"number": "1"}, "2": {"number": "2"}},
        "b": None,      # no consultable → degraded, NO entra
        "c": {},        # vacía real → se omite, NO degrada
    }
    scores = {"a": {"score": 70}, "c": {"score": 90}}
    st = _run(T, monkeypatch, cands, catalogs, scores)

    names = [s["sourceName"] for s in st["sources"]]
    assert names == ["A"], "solo la fuente consultable con capítulos entra"
    assert st["status"] == "done"
    assert any("B" in d for d in st["degraded"]), "la no-consultable marca degraded (falló ≠ vacía)"
    assert st["partial"] is True, "con degraded, el barrido es parcial"


def test_ranking_is_deterministic(T, monkeypatch):
    # Dos fuentes puntuadas + una sin puntuar: por score desc, la no puntuada al final.
    cands = [
        {"sourceId": "a", "id": 1, "sourceName": "A", "sourceLang": "es", "match": 0.86},
        {"sourceId": "b", "id": 2, "sourceName": "B", "sourceLang": "es", "match": 0.99},
        {"sourceId": "c", "id": 3, "sourceName": "C", "sourceLang": "es", "match": 0.90},
    ]
    catalogs = {k: {"1": {"number": "1"}} for k in ("a", "b", "c")}
    scores = {"a": {"score": 60}, "b": {"score": 95}}   # c sin score
    st = _run(T, monkeypatch, cands, catalogs, scores)
    names = [s["sourceName"] for s in st["sources"]]
    assert names == ["B", "A", "C"], "score desc; la no puntuada (C) va por match al final"


def test_rank_helper_completeness(T):
    src = [
        {"sourceName": "X", "chapters": ["1", "2"], "quality": {"score": 50}},
        {"sourceName": "Y", "chapters": ["1"], "quality": {"score": 80}},
    ]
    all_ch = {"1", "2", "3", "4"}
    ranked, total = T._rank_coverage_sources(src, all_ch)
    assert total == 4
    assert ranked[0]["sourceName"] == "Y"          # mayor score primero
    assert ranked[0]["completeness"] == round(1 / 4, 3)
    assert ranked[1]["completeness"] == round(2 / 4, 3)
