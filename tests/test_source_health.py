"""Histórico de aciertos por fuente (acota el fan-out del descubrimiento).

Costuras con riesgo de FALLO SILENCIOSO que se blindan aquí:
- una fuente que ALGUNA vez acertó no debe degradarse jamás (perderíamos una versión ya vista);
- una fuente FRÍA no se descarta: el round-robin la re-sondea en ≤_PROBE_EVERY rondas;
- histórico ausente/corrupto ⇒ buscar TODAS (falló ≠ 'no hay'), nunca reducir por un fallo de I/O;
- una fuente NUEVA (sin historial) es caliente por defecto;
- record cuenta 'respondió' aparte de 'acertó' (un timeout no penaliza el hit-rate).

Correr:  .venv/bin/python -m pytest tests/test_source_health.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api import source_health as SH  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # aislar el fichero de estado y el estado en memoria por test
    monkeypatch.setattr(SH, '_PATH', tmp_path / 'source_health.json')
    monkeypatch.setattr(SH, '_OFF', False)
    SH._reset_for_test()
    yield
    SH._reset_for_test()


ALL = [str(i) for i in range(20)]


def _make_cold(sid, resp=None):
    """Responde `resp` veces (≥_COLD_AFTER) sin acertar nunca → deja `sid` frío."""
    resp = resp if resp is not None else SH._COLD_AFTER
    for _ in range(resp):
        keep, seq = SH.select_sources(ALL)
        SH.record(seq, responded_ids=[sid], hit_ids=[])


def test_new_source_is_hot():
    # sin historial: TODAS se buscan
    keep, seq = SH.select_sources(ALL)
    assert set(keep) == set(ALL)
    assert seq == 1


def test_ever_hit_is_never_cold():
    # una fuente que acierta UNA vez y luego responde vacío muchísimas veces sigue caliente
    keep, seq = SH.select_sources(ALL)
    SH.record(seq, responded_ids=['3'], hit_ids=['3'])
    for _ in range(SH._COLD_AFTER * 3):
        keep, seq = SH.select_sources(ALL)
        SH.record(seq, responded_ids=['3'], hit_ids=[])   # ya nunca acierta
    keep, _ = SH.select_sources(ALL)
    assert '3' in keep, "una fuente que acertó alguna vez NUNCA debe degradarse a fría"


def test_never_hit_becomes_cold_and_is_skipped_sometimes():
    _make_cold('7')
    # tras _COLD_AFTER respuestas vacías, '7' es fría; en la mayoría de rondas NO se busca
    skipped = 0
    for _ in range(SH._PROBE_EVERY):
        keep, seq = SH.select_sources(ALL)
        if '7' not in keep:
            skipped += 1
        SH.record(seq, responded_ids=[s for s in keep], hit_ids=[])
    assert skipped >= 1, "una fuente fría debería saltarse en algunas rondas"


def test_cold_source_is_still_reprobed_within_probe_every():
    # muchas frías: cada una debe re-sondearse al menos una vez en _PROBE_EVERY rondas (round-robin)
    for sid in ALL:
        _make_cold(sid)
    probed = set()
    for _ in range(SH._PROBE_EVERY):
        keep, seq = SH.select_sources(ALL)
        probed |= set(keep)
        SH.record(seq, responded_ids=list(keep), hit_ids=[])
    assert probed == set(ALL), "el round-robin debe cubrir TODAS las frías en _PROBE_EVERY rondas"


def test_missing_history_searches_all():
    # primera vez, sin fichero: todas
    keep, _ = SH.select_sources(ALL)
    assert set(keep) == set(ALL)


def test_corrupt_history_degrades_to_search_all(tmp_path, monkeypatch):
    p = SH._PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not valid json ", encoding='utf-8')
    SH._reset_for_test()
    keep, _ = SH.select_sources(ALL)
    assert set(keep) == set(ALL), "histórico corrupto ⇒ buscar TODAS (falló ≠ 'no hay')"


def test_off_switch_searches_all(monkeypatch):
    monkeypatch.setattr(SH, '_OFF', True)
    for sid in ALL:
        pass
    # aunque hubiera frías, OFF devuelve todas
    keep, seq = SH.select_sources(ALL)
    assert set(keep) == set(ALL) and seq == 0


def test_failed_response_does_not_count_as_no_hit():
    # si una fuente NO respondió (no está en responded_ids), no debe acumular 'resp' → no se enfría
    for _ in range(SH._COLD_AFTER * 2):
        keep, seq = SH.select_sources(ALL)
        SH.record(seq, responded_ids=[], hit_ids=[])   # '9' nunca respondió (timeouts)
    keep, _ = SH.select_sources(ALL)
    assert '9' in keep, "los timeouts (no respondió) no deben enfriar una fuente"


def test_persists_across_reload(tmp_path):
    _make_cold('5')
    SH._reset_for_test()   # simula reinicio: se recarga del disco
    s = SH.stats()
    assert s['tracked'] >= 1 and s['cold'] >= 1
