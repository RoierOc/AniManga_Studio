"""Escaneo de idiomas por lote: la clasificación embedded/sidecar/none y el 'recommended'.

Es la costura que decide qué se salta y qué se traduce; un fallo aquí traduciría español a español
o saltaría un episodio que sí lo necesita.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from api import subtitle_batch as sb  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    sb._scan_cache.clear()
    yield
    sb._scan_cache.clear()


def _patch(monkeypatch, tracks, sidecar=False, exists=True):
    monkeypatch.setattr(sb, "_ffprobe_tracks", lambda p: tracks)
    monkeypatch.setattr(sb.os.path, "getmtime", lambda p: 123)
    # `is_es_track` es la real (clasifica por código+título) → prueba la integración de verdad.
    monkeypatch.setattr("api.anime._es_sub_injected", lambda p: sidecar)
    monkeypatch.setattr(sb, "_resolve_item_path", lambda item: ("/v.mkv" if exists else None))


def test_embedded_spanish_se_salta(monkeypatch):
    _patch(monkeypatch, [{"language": "spa", "title": ""}, {"language": "eng", "title": ""}])
    r = sb._scan_one({"episode": 1})
    assert r["es_status"] == "embedded"
    assert r["recommended"] == "skip"
    assert r["source_langs"] == ["eng"]          # la ES no cuenta como fuente a traducir


def test_sidecar_nuestro_tambien_se_salta(monkeypatch):
    """Un .spa.ass que ya generamos cuenta como 'ya hay español', pero se distingue del embebido."""
    _patch(monkeypatch, [{"language": "eng", "title": ""}], sidecar=True)
    r = sb._scan_one({"episode": 2})
    assert r["es_status"] == "sidecar"
    assert r["recommended"] == "skip"


def test_ingles_sin_espanol_se_procesa(monkeypatch):
    _patch(monkeypatch, [{"language": "eng", "title": ""}, {"language": "jpn", "title": ""}])
    r = sb._scan_one({"episode": 3})
    assert r["es_status"] == "none"
    assert r["recommended"] == "process"
    assert r["source_langs"] == ["eng", "jpn"]
    assert r["has_text_tracks"] is True


def test_sin_pistas_de_texto_igual_se_procesa_via_premade(monkeypatch):
    """Un concierto/BD sin subs de texto: no hay fuente que traducir, pero se puede BUSCAR premade
    → 'process'. `has_text_tracks=False` avisa a la UI de que la única vía es la búsqueda."""
    _patch(monkeypatch, [])
    r = sb._scan_one({"episode": 4})
    assert r["recommended"] == "process"
    assert r["has_text_tracks"] is False


def test_archivo_ausente_no_es_lo_mismo_que_sin_subtitulos(monkeypatch):
    """Falló≠no-había: sin archivo se reporta aparte, nunca como 'sin español que traducir'."""
    _patch(monkeypatch, [], exists=False)
    r = sb._scan_one({"episode": 5})
    assert r["file_present"] is False
    assert r["recommended"] == "missing_file"


def test_reconoce_espanol_latino_por_el_titulo(monkeypatch):
    """is_es_track mira código Y título: una pista 'und' titulada 'Español (Latinoamérica)' es ES."""
    _patch(monkeypatch, [{"language": "und", "title": "Español Latino"}])
    r = sb._scan_one({"episode": 6})
    assert r["es_status"] == "embedded"
    assert r["recommended"] == "skip"


# ── worker: el árbol de decisión por episodio ──────────────────────────────────
_BATCH = {"batch_id": "test"}


def _worker_patch(monkeypatch, tracks, sidecar=False, premade=None, path="/v.mkv"):
    monkeypatch.setattr(sb, "_resolve_item_path", lambda item: path)
    monkeypatch.setattr(sb, "_ffprobe_tracks", lambda p: tracks)
    monkeypatch.setattr("api.anime._es_sub_injected", lambda p: sidecar)
    monkeypatch.setattr(sb, "_ext_find_spanish_subs", lambda titles, ep, season=1: premade or [])
    monkeypatch.setattr(sb, "_titles_of", lambda item: ["Show"])


def test_worker_salta_si_ya_hay_espanol(monkeypatch):
    _worker_patch(monkeypatch, [{"language": "spa", "title": ""}])
    r = sb._process_episode(_BATCH, {"episode": 1}, "buscar_o_traducir", "spa", force=False)
    assert r["status"] == "skipped"


def test_worker_usa_premade_antes_que_ia(monkeypatch):
    """Premade primero: si OpenSubtitles/etc. lo tienen, se inyecta sin tocar la IA."""
    _worker_patch(monkeypatch, [{"language": "eng", "title": "", "sub_index": 0, "codec": "ass"}],
                  premade=[{"source": "opensubtitles"}])
    monkeypatch.setattr(sb, "_ext_download_sub", lambda info, td: "/tmp/s.srt")
    monkeypatch.setattr(sb, "_sync_sub_to_reference", lambda s, m, td: s)
    injected = {}
    monkeypatch.setattr(sb, "_inject_sub", lambda p, s, n: injected.setdefault("hit", True))
    called_ia = {"n": 0}
    monkeypatch.setattr(sb, "_do_translate", lambda *a, **k: called_ia.__setitem__("n", 1))
    r = sb._process_episode(_BATCH, {"episode": 2}, "buscar_o_traducir", "spa", force=False)
    assert r["status"] == "done" and r["method"] == "premade"
    assert injected.get("hit") and called_ia["n"] == 0, "no debió tocar la IA teniendo premade"


def test_worker_solo_buscar_no_traduce_con_ia(monkeypatch):
    """Política solo_buscar: sin premade → error explícito, NUNCA cae a la IA."""
    _worker_patch(monkeypatch, [{"language": "eng", "title": "", "sub_index": 0, "codec": "ass"}], premade=[])
    monkeypatch.setattr(sb, "_do_translate", lambda *a, **k: pytest.fail("no debía traducir con IA"))
    r = sb._process_episode(_BATCH, {"episode": 3}, "solo_buscar", "spa", force=False)
    assert r["status"] == "error" and "premade" in r["message"]


def test_worker_traduce_con_ia_si_no_hay_premade(monkeypatch):
    """buscar_o_traducir sin premade → IA (Ollama). Elige la pista inglesa como fuente."""
    _worker_patch(monkeypatch, [{"language": "jpn", "title": "", "sub_index": 0, "codec": "ass"},
                                {"language": "eng", "title": "", "sub_index": 1, "codec": "ass"}],
                  premade=[])
    captured = {}

    def fake_translate(child_id, path, sub_index, codec, n, src, force_engine=None):
        captured.update(sub_index=sub_index, src=src, engine=force_engine)
        sb._tasks[child_id] = {"status": "done"}
    monkeypatch.setattr(sb, "_do_translate", fake_translate)
    monkeypatch.setattr(sb, "_mk_sub_task", lambda *a, **k: sb._tasks.__setitem__(a[0], {}))
    r = sb._process_episode(_BATCH, {"episode": 4}, "buscar_o_traducir", "spa", force=False)
    assert r["status"] == "done" and r["method"] == "ia"
    assert captured["engine"] == "ollama"           # motor forzado a Ollama (Gemini retirado)
    assert captured["sub_index"] == 1 and captured["src"] == "eng"   # eligió la pista inglesa


def test_worker_sin_fuente_ni_premade_es_error(monkeypatch):
    _worker_patch(monkeypatch, [], premade=[])      # concierto sin subs de texto
    r = sb._process_episode(_BATCH, {"episode": 5}, "solo_ia", "spa", force=False)
    assert r["status"] == "error" and "texto" in r["message"]
