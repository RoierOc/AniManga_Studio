"""Contrato del caché de snapshots que protege el escaneo de la Biblioteca."""
import pytest

from api.library_cache import SnapshotCache


def _cache(clock, refreshers):
    return SnapshotCache(
        ttl=10,
        clock=lambda: clock[0],
        start_thread=lambda target: refreshers.append(target),
    )


def test_repite_la_foto_sin_volver_a_ejecutar_el_loader():
    clock = [0]
    refreshers = []
    cache = _cache(clock, refreshers)
    calls = []

    def load():
        calls.append('load')
        return ['Obra']

    assert cache.get('normal', load) == ['Obra']
    assert cache.get('normal', load) == ['Obra']
    assert calls == ['load']
    assert refreshers == []


def test_caducado_sirve_stale_y_lanza_un_solo_refresco():
    clock = [0]
    refreshers = []
    cache = _cache(clock, refreshers)
    version = [0]

    def load():
        version[0] += 1
        return [version[0]]

    assert cache.get('normal', load) == [1]
    clock[0] = 11

    assert cache.get('normal', load) == [1]
    assert cache.get('normal', load) == [1]
    assert len(refreshers) == 1

    refreshers.pop()()
    assert cache.get('normal', load) == [2]


def test_el_refresco_puede_conservar_el_contexto_de_la_biblioteca():
    clock = [0]
    refreshers = []
    cache = _cache(clock, refreshers)

    assert cache.get('hidden', lambda: ['normal']) == ['normal']
    clock[0] = 11
    assert cache.get(
        'hidden',
        lambda: pytest.fail('el refresco no debe usar el loader sin contexto'),
        refresh_loader=lambda: ['hidden'],
    ) == ['normal']

    refreshers.pop()()
    assert cache.get('hidden', lambda: ['normal']) == ['hidden']
