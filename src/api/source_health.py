"""Histórico de aciertos por fuente para ACOTAR el fan-out del descubrimiento.

El problema (medido): el descubrimiento de versiones barre las ~558 fuentes de Suwayomi (×~2.4
variantes = ~1336 búsquedas). Pero ~80% de las fuentes NO devuelven nada para un manga dado, y una
gran parte son mirrors/agregadores muertos que no aciertan JAMÁS. Barrerlas todas cada vez es el
grueso del tiempo de "buscar la mejor versión".

La idea (la más SEGURA posible): aprender qué fuentes aciertan. Una fuente que **alguna vez** produjo
un candidato es CALIENTE para siempre (nunca se degrada → nunca perdemos una versión que ya vimos).
Solo se degradan a FRÍO las que RESPONDIERON `_COLD_AFTER` veces y **nunca** dieron nada. Las frías
NO se descartan: se re-sondean por round-robin (las de sondeo más antiguo primero), de modo que una
fuente fría que MÁS TARDE gane el manga se re-descubre en ≤`_PROBE_EVERY` descubrimientos.

Fail-safe («falló ≠ no había»): si no hay histórico o está corrupto, se
buscan TODAS las fuentes. Nunca se reduce el conjunto por un fallo de lectura. Una fuente NUEVA (sin
historial) es CALIENTE por defecto hasta que demuestre estar muerta.

Separación mecanismo/constante (regla de arreglos globales): la política vive aquí; los umbrales son
constantes al principio. `SOURCE_HEALTH_OFF=1` lo desactiva por completo (búsqueda íntegra).

Persistencia: un JSON en el dir de caché (`source_health.json`), escritura atómica bajo lock. No usa
la caché con TTL: son estadísticas de largo plazo, no un valor que caduque.
"""
import json
import os
import threading

from api.observability import record_error
from api.runtime import _CACHE_DIR

# ── Constantes de política (mecanismo separado de estos valores) ──────────────────────────
_COLD_AFTER = 8      # respondió ≥N veces con 0 aciertos → candidata a FRÍA
_PROBE_EVERY = 10    # una fuente FRÍA se re-sondea al menos 1 de cada N descubrimientos
_OFF = os.environ.get("SOURCE_HEALTH_OFF") == "1"

_PATH = _CACHE_DIR / "source_health.json"
_lock = threading.Lock()
_state = None   # {"seq": int, "sources": {sid: {"resp": int, "hits": int, "lastProbe": int}}}


def _load():
    global _state
    if _state is not None:
        return _state
    try:
        with open(_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("sources"), dict):
            _state = {"seq": int(d.get("seq") or 0), "sources": d["sources"]}
            return _state
    except FileNotFoundError:
        pass   # primera vez: no es un fallo, es que aún no hay histórico
    except Exception as e:
        # Corrupto/ilegible: SÍ es un fallo → lo hacemos visible, pero degradamos a "sin
        # histórico" (buscar todas) en vez de romper el descubrimiento.
        record_error("transplant", e, op="source_health_load",
                     note="histórico ilegible; se buscarán TODAS las fuentes")
    _state = {"seq": 0, "sources": {}}
    return _state


def _save(st):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f"{_PATH}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, _PATH)   # atómico: nunca deja un JSON a medias
    except Exception as e:
        record_error("transplant", e, op="source_health_save",
                     note="no se pudo persistir el histórico de fuentes")


def _is_cold(rec) -> bool:
    """FRÍA = respondió muchas veces y NUNCA acertó. Si acertó alguna vez → nunca fría."""
    return bool(rec) and rec.get("hits", 0) == 0 and rec.get("resp", 0) >= _COLD_AFTER


def select_sources(all_ids: list) -> tuple[list, int]:
    """Dado el universo de ids de fuente, devuelve (ids_a_buscar, seq) aplicando la política.
    CALIENTES (o sin historial, o que alguna vez acertaron) siempre; FRÍAS solo una porción por
    round-robin (las de sondeo más antiguo). `seq` identifica esta ronda para `record`.

    Fail-safe: con OFF, sin historial o ante cualquier duda → devuelve TODAS."""
    all_ids = [str(x) for x in all_ids]
    if _OFF or not all_ids:
        return all_ids, 0
    with _lock:
        st = _load()
        seq = st["seq"] + 1
        st["seq"] = seq
        srcs = st["sources"]
        hot, cold = [], []
        for sid in all_ids:
            (cold if _is_cold(srcs.get(sid)) else hot).append(sid)
        if not cold:
            return all_ids, seq
        # Re-sondear la fracción de FRÍAS con `lastProbe` más antiguo → cobertura garantizada:
        # cada fría se prueba al menos 1 de cada _PROBE_EVERY rondas, empezando por las más rezagadas.
        budget = max(1, (len(cold) + _PROBE_EVERY - 1) // _PROBE_EVERY)
        cold.sort(key=lambda sid: srcs.get(sid, {}).get("lastProbe", 0))
        probe = cold[:budget]
        return hot + probe, seq


def record(seq: int, responded_ids, hit_ids):
    """Tras un descubrimiento: `responded_ids` = fuentes que RESPONDIERON (no timeout/None);
    `hit_ids` = las que produjeron ≥1 candidato válido. Actualiza contadores y persiste.
    Un fallo de red (no respondió) NO cuenta como 'respondió sin acierto' — falló ≠ vacía."""
    if _OFF or not seq:
        return
    responded = {str(x) for x in responded_ids}
    hits = {str(x) for x in hit_ids}
    with _lock:
        st = _load()
        srcs = st["sources"]
        for sid in responded:
            rec = srcs.setdefault(sid, {"resp": 0, "hits": 0, "lastProbe": 0})
            rec["resp"] += 1
            rec["lastProbe"] = seq
            if sid in hits:
                rec["hits"] += 1
        _save(st)


def stats() -> dict:
    """Resumen para diagnóstico (/api rara, o tests): totales por tier."""
    with _lock:
        st = _load()
        srcs = st["sources"]
        cold = sum(1 for r in srcs.values() if _is_cold(r))
        ever_hit = sum(1 for r in srcs.values() if r.get("hits", 0) > 0)
        return {"tracked": len(srcs), "cold": cold, "everHit": ever_hit,
                "seq": st["seq"], "off": _OFF}


def _reset_for_test():
    """Solo para tests: limpia el estado en memoria (no toca disco)."""
    global _state
    _state = None
