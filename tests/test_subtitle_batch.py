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
    monkeypatch.setattr(sb, "_ext_find_spanish_subs",
                        lambda titles, ep, season=1, report=None: premade or [])
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

    def fake_translate(child_id, path, sub_index, codec, n, src, external_sub_info=None):
        captured.update(sub_index=sub_index, src=src)
        sb._tasks[child_id] = {"status": "done"}
    monkeypatch.setattr(sb, "_do_translate", fake_translate)
    monkeypatch.setattr(sb, "_mk_sub_task", lambda *a, **k: sb._tasks.__setitem__(a[0], {}))
    r = sb._process_episode(_BATCH, {"episode": 4}, "buscar_o_traducir", "spa", force=False)
    assert r["status"] == "done" and r["method"] == "ia"
    assert captured["sub_index"] == 1 and captured["src"] == "eng"   # eligió la pista inglesa


def test_worker_sin_fuente_ni_premade_es_error(monkeypatch):
    _worker_patch(monkeypatch, [], premade=[])      # concierto sin subs de texto
    r = sb._process_episode(_BATCH, {"episode": 5}, "solo_ia", "spa", force=False)
    assert r["status"] == "error" and "texto" in r["message"]


# ── Reinyectar (rehacer un subtítulo que salió mal) ────────────────────────────

def test_force_REHACE_un_episodio_que_ya_tiene_espanol(monkeypatch):
    """Sin `force` el worker lo salta; con `force` vuelve a buscar/traducir. Es la única vía para
    repetir un subtítulo que quedó mal (desincronizado, traducción pésima)."""
    _worker_patch(monkeypatch, [{"language": "eng", "title": "", "sub_index": 0, "codec": "ass"}],
                  sidecar=True, premade=[{"source": "opensubtitles"}])
    monkeypatch.setattr(sb, "_ext_download_sub", lambda info, td: "/tmp/s.srt")
    monkeypatch.setattr(sb, "_sync_sub_to_reference", lambda s, m, td: s)
    monkeypatch.setattr(sb, "_inject_sub", lambda p, s, n: p)

    assert sb._process_episode(_BATCH, {"episode": 7}, "buscar_o_traducir", "spa",
                               force=False)["status"] == "skipped"
    assert sb._process_episode(_BATCH, {"episode": 7}, "buscar_o_traducir", "spa",
                               force=True)["status"] == "done"


def test_rehacer_SUSTITUYE_el_sidecar_anterior_no_lo_duplica(tmp_path):
    """El sidecar anterior era `.spa.srt` y el nuevo sale `.spa.ass`: si no se borra el viejo, el
    reproductor lista DOS pistas «Español» y elige la que no toca."""
    from api.subtitle import _save_sub_external
    video = tmp_path / "ep01.mkv"; video.write_bytes(b"v")
    (tmp_path / "ep01.spa.srt").write_text("viejo")
    nuevo = tmp_path / "nuevo.ass"; nuevo.write_text("nuevo")

    out = _save_sub_external(str(video), str(nuevo))
    assert sorted(p.name for p in tmp_path.iterdir() if ".spa." in p.name) == ["ep01.spa.ass"]
    assert Path(out).read_text() == "nuevo"
    assert (tmp_path / "ep01.es_injected").exists()


def test_borrar_sidecars_no_toca_el_video_ni_otros_episodios(tmp_path):
    from api.subtitle import _drop_es_sidecars
    (tmp_path / "ep01.mkv").write_bytes(b"v")
    (tmp_path / "ep01.spa.ass").write_text("x")
    (tmp_path / "ep01.eng.srt").write_text("x")     # no es nuestro
    (tmp_path / "ep02.spa.ass").write_text("x")     # otro episodio

    assert _drop_es_sidecars(str(tmp_path / "ep01.mkv")) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ep01.eng.srt", "ep01.mkv", "ep02.spa.ass"]


# ── Fuente elegida a mano por episodio ────────────────────────────────────────

def test_la_eleccion_manual_manda_sobre_la_politica(monkeypatch):
    """Con política 'solo_ia' pero fuente premade elegida a mano, se usa la elegida: el usuario ya
    miró las opciones de ESE episodio y decidió."""
    _worker_patch(monkeypatch, [{"language": "eng", "title": "", "sub_index": 0, "codec": "ass"}])
    monkeypatch.setattr(sb, "_ext_download_sub", lambda info, td: "/tmp/s.srt")
    monkeypatch.setattr(sb, "_sync_sub_to_reference", lambda s, m, td: s)
    monkeypatch.setattr(sb, "_inject_sub", lambda p, s, n: p)
    monkeypatch.setattr(sb, "_do_translate", lambda *a, **k: pytest.fail("no debió tocar la IA"))

    item = {"episode": 8, "choice": {"kind": "premade", "info": {"source": "subdivx"}}}
    r = sb._process_episode(_BATCH, item, "solo_ia", "spa", force=False)
    assert r["status"] == "done" and r["method"] == "premade" and "subdivx" in r["message"]


def test_elegir_fuente_implica_rehacer_aunque_ya_tenga_espanol(monkeypatch):
    """Sin elección se saltaría por 'ya tiene español'; elegir fuente ES la intención de rehacerlo."""
    _worker_patch(monkeypatch, [{"language": "eng", "title": "", "sub_index": 0, "codec": "ass"}],
                  sidecar=True)
    monkeypatch.setattr(sb, "_ext_download_sub", lambda info, td: "/tmp/s.srt")
    monkeypatch.setattr(sb, "_sync_sub_to_reference", lambda s, m, td: s)
    monkeypatch.setattr(sb, "_inject_sub", lambda p, s, n: p)
    item = {"episode": 9, "choice": {"kind": "premade", "info": {"source": "nyaa"}}}
    assert sb._process_episode(_BATCH, item, "buscar_o_traducir", "spa", force=False)["status"] == "done"


def test_la_pista_elegida_se_traduce_POR_SU_sub_index(monkeypatch):
    """El bug histórico: mandar el índice de otra lista traduce una pista distinta a la elegida,
    y el fallo es MUDO (sale un subtítulo, pero del idioma equivocado)."""
    _worker_patch(monkeypatch, [{"language": "ara", "title": "", "sub_index": 0, "codec": "ass"},
                                {"language": "eng", "title": "", "sub_index": 3, "codec": "srt"}])
    cap = {}

    def fake(child_id, path, sub_index, codec, n, src, external_sub_info=None):
        cap.update(sub_index=sub_index, codec=codec, src=src)
        sb._tasks[child_id] = {"status": "done"}
    monkeypatch.setattr(sb, "_do_translate", fake)
    monkeypatch.setattr(sb, "_mk_sub_task", lambda *a, **k: sb._tasks.__setitem__(a[0], {}))

    item = {"episode": 10, "choice": {"kind": "track", "sub_index": 3}}
    r = sb._process_episode(_BATCH, item, "buscar_o_traducir", "spa", force=True)
    assert r["status"] == "done" and cap == {"sub_index": 3, "codec": "srt", "src": "eng"}


def test_si_la_pista_elegida_ya_no_esta_se_DECLARA_no_se_cambia_en_silencio(monkeypatch):
    _worker_patch(monkeypatch, [{"language": "ara", "title": "", "sub_index": 0, "codec": "ass"}])
    monkeypatch.setattr(sb, "_do_translate", lambda *a, **k: pytest.fail("no debió traducir otra"))
    r = sb._process_episode(_BATCH, {"episode": 11, "choice": {"kind": "track", "sub_index": 7}},
                            "buscar_o_traducir", "spa", force=True)
    assert r["status"] == "error" and "0=ara" in r["message"]


def test_una_eleccion_que_revienta_no_tumba_el_lote(monkeypatch):
    _worker_patch(monkeypatch, [{"language": "eng", "title": "", "sub_index": 0, "codec": "ass"}])
    monkeypatch.setattr(sb, "_ext_download_sub", lambda info, td: (_ for _ in ()).throw(RuntimeError("403")))
    r = sb._process_episode(_BATCH, {"episode": 12, "choice": {"kind": "premade", "info": {}}},
                            "buscar_o_traducir", "spa", force=True)
    assert r["status"] == "error" and "403" in r["message"]


# ── El lote sobrevive a un reinicio (como información, no como proceso) ────────

def test_un_lote_terminado_se_recupera_tras_reiniciar(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "_state_path", lambda: str(tmp_path / "b.json"))
    sb._batches.clear()
    sb._batches["b1"] = {"batch_id": "b1", "status": "done", "done": 3, "total": 3,
                         "items": [{"episode": 1, "status": "done"}], "_items": [{"episode": 1}]}
    sb._save_batches()

    sb._batches.clear()                      # ← el reinicio
    sb.load_batches()
    assert sb._batches["b1"]["done"] == 3
    assert "_items" not in sb._batch_public(sb._batches["b1"])


def test_un_lote_EN_CURSO_se_declara_roto_no_se_finge_vivo(tmp_path, monkeypatch):
    """Su hilo murió con el proceso: dejarlo en 'running' sería una barra que no avanza nunca."""
    monkeypatch.setattr(sb, "_state_path", lambda: str(tmp_path / "b.json"))
    sb._batches.clear()
    sb._batches["b2"] = {"batch_id": "b2", "status": "running", "done": 1, "total": 3,
                         "items": [{"episode": 1, "status": "done"},
                                   {"episode": 2, "status": "processing"},
                                   {"episode": 3, "status": "pending"}],
                         "_items": []}
    sb._save_batches()

    sb._batches.clear()
    sb.load_batches()
    b = sb._batches["b2"]
    assert b["status"] == "interrupted" and "reinici" in b["error"]
    assert b["items"][0]["status"] == "done", "lo ya hecho NO se pierde: el subtítulo está en disco"
    assert [i["status"] for i in b["items"][1:]] == ["cancelled", "cancelled"]


def test_sin_fichero_de_estado_no_pasa_nada(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "_state_path", lambda: str(tmp_path / "no-existe.json"))
    sb._batches.clear()
    sb.load_batches()
    assert sb._batches == {}
