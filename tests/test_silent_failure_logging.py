"""El store de la biblioteca ya no confunde "no había" con "falló" — y cuando falla, deja rastro.

`_lib_read`/`_history_read` devolvían `{}`/`[]` ante CUALQUIER problema: fichero ausente (vacío
legítimo) y fichero corrupto/ilegible (fallo real) daban el MISMO valor, así que toda la app
(storage, backup, progreso nativo) veía "0 animes" sin ninguna huella. Es el error más caro del
proyecto ("falló ≠ vacío"). Ahora:
  · ausente → sigue devolviendo vacío y NO registra nada (no es un fallo);
  · corrupto → sigue devolviendo vacío (comportamiento intacto) PERO registra el error (contador
    'anime' + evento SSE), así que el fallo es distinguible del vacío.

Estos tests fijan justo esa distinción. `api.anime`/`api.observability` no arrastran cv2/torch,
así que corren en el CI ligero. Ver [[project_hardening_phases]].

Correr:  .venv/bin/python -m pytest tests/test_silent_failure_logging.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api import observability as obs  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    obs.reset_error_counts()
    yield
    obs.reset_error_counts()


# ── _lib_read ─────────────────────────────────────────────────────────────────
def test_lib_read_missing_file_is_empty_not_an_error(tmp_path, monkeypatch):
    import api.anime as A
    monkeypatch.setattr(A, "_lib_path", lambda: tmp_path / "anime_library.json")
    assert A._lib_read() == {}
    assert obs.error_counts().get("anime") is None   # vacío legítimo: sin ruido


def test_lib_read_corrupt_file_returns_empty_but_logs(tmp_path, monkeypatch):
    import api.anime as A
    p = tmp_path / "anime_library.json"
    p.write_text("{ esto no es json valido ", encoding="utf-8")
    monkeypatch.setattr(A, "_lib_path", lambda: p)
    # Comportamiento intacto: sigue devolviendo {} (no revienta el endpoint que lo llama)...
    assert A._lib_read() == {}
    # ...pero el fallo YA es distinguible del vacío.
    assert obs.error_counts().get("anime") == 1


def test_lib_read_valid_file_roundtrips_without_logging(tmp_path, monkeypatch):
    import api.anime as A
    p = tmp_path / "anime_library.json"
    p.write_text('{"a1": {"title": "X"}}', encoding="utf-8")
    monkeypatch.setattr(A, "_lib_path", lambda: p)
    assert A._lib_read() == {"a1": {"title": "X"}}
    assert obs.error_counts().get("anime") is None


# ── _history_read ─────────────────────────────────────────────────────────────
def test_history_read_missing_is_empty_not_an_error(tmp_path, monkeypatch):
    import api.anime as A
    from api import history_store
    monkeypatch.setattr(history_store, "manga_dir", lambda: tmp_path)
    assert A._history_read() == []
    assert obs.error_counts().get("history") is None


def test_history_read_corrupt_returns_empty_but_logs(tmp_path, monkeypatch):
    # El historial ya no vive dentro de anime.py: lo gobierna `api/history_store.py`, que
    # archiva por meses en vez de truncar a 500. La costura a pinchar es su `manga_dir`, y el
    # componente que cuenta es 'history'. Lo que se comprueba no cambia: corrupto ≠ vacío.
    import api.anime as A
    from api import history_store
    (tmp_path / "watch_history.json").write_text("no-json", encoding="utf-8")
    monkeypatch.setattr(history_store, "manga_dir", lambda: tmp_path)
    assert A._history_read() == []
    assert obs.error_counts().get("history") == 1


# ── config de usuario (rutas de escaneo / ajustes): corrupta ≠ sin configurar ──
def test_scanpaths_missing_is_default_not_an_error(tmp_path, monkeypatch):
    import api.anime as A
    monkeypatch.setattr(A, "_scanpaths_path", lambda: tmp_path / "anime_scan_paths.json")
    assert A._scanpaths_read() == {"paths": [], "mappings": {}}
    assert obs.error_counts().get("anime") is None


def test_scanpaths_corrupt_logs_because_it_would_lose_user_folders(tmp_path, monkeypatch):
    import api.anime as A
    p = tmp_path / "anime_scan_paths.json"
    p.write_text("{roto", encoding="utf-8")
    monkeypatch.setattr(A, "_scanpaths_path", lambda: p)
    assert A._scanpaths_read() == {"paths": [], "mappings": {}}
    assert obs.error_counts().get("anime") == 1


def test_anime_settings_corrupt_logs(tmp_path, monkeypatch):
    import api.anime as A
    p = tmp_path / "anime_settings.json"
    p.write_text("{roto", encoding="utf-8")
    monkeypatch.setattr(A, "_anime_settings_path", lambda: p)
    assert A._anime_settings_read() == {}
    assert obs.error_counts().get("anime") == 1


# ── caché en disco (MAL stacks): corrupto se registra, ausente no ─────────────
def test_stacks_cache_missing_is_silent(tmp_path, monkeypatch):
    import api.anime as A
    monkeypatch.setattr(A, "_STACKS_CACHE_PATH", tmp_path / "stacks.json")
    A._load_stacks_cache()
    assert obs.error_counts().get("anime") is None


def test_stacks_cache_corrupt_logs(tmp_path, monkeypatch):
    import api.anime as A
    p = tmp_path / "stacks.json"
    p.write_text("no-json", encoding="utf-8")
    monkeypatch.setattr(A, "_STACKS_CACHE_PATH", p)
    A._load_stacks_cache()   # no revienta el arranque
    assert obs.error_counts().get("anime") == 1


# ── caché de duraciones (load) ────────────────────────────────────────────────
def test_dur_cache_missing_is_silent(tmp_path, monkeypatch):
    import api.anime as A
    monkeypatch.setattr(A, "_DUR_CACHE_PATH", tmp_path / "dur.json")
    A._load_dur_cache()
    assert obs.error_counts().get("anime") is None


def test_dur_cache_corrupt_logs(tmp_path, monkeypatch):
    import api.anime as A
    p = tmp_path / "dur.json"
    p.write_text("{roto", encoding="utf-8")
    monkeypatch.setattr(A, "_DUR_CACHE_PATH", p)
    A._load_dur_cache()
    assert obs.error_counts().get("anime") == 1


# ── caminos de ESCRITURA (swallow): fallo → registrado, sin propagar ──────────
def _unwritable(tmp_path):
    """Ruta cuyo PADRE es un fichero → open(...,'w') lanza NotADirectoryError."""
    blocker = tmp_path / "soy_un_fichero"
    blocker.write_text("x", encoding="utf-8")
    return blocker / "hijo.json"


def test_history_append_write_failure_is_logged_not_raised(tmp_path, monkeypatch):
    import api.anime as A
    from api import history_store
    # Carpeta de biblioteca que es en realidad un FICHERO → cualquier escritura dentro lanza.
    blocker = tmp_path / "soy_un_fichero"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(history_store, "manga_dir", lambda: blocker)
    # No debe propagar aunque la escritura falle:
    A._history_append("a1", "Serie", 3, cover="")
    assert obs.error_counts().get("history") == 1


def test_history_append_write_success_logs_nothing(tmp_path, monkeypatch):
    import api.anime as A
    from api import history_store
    monkeypatch.setattr(history_store, "manga_dir", lambda: tmp_path)
    A._history_append("a1", "Serie", 3, cover="")
    assert (tmp_path / "watch_history.json").exists()
    assert obs.error_counts().get("history") is None


def test_dur_cache_save_failure_is_logged_not_raised(tmp_path, monkeypatch):
    import api.anime as A
    monkeypatch.setattr(A, "_DUR_CACHE_PATH", _unwritable(tmp_path))
    monkeypatch.setattr(A, "_dur_cache_dirty", True)
    A._save_dur_cache()
    assert obs.error_counts().get("anime") == 1


def test_stacks_cache_save_failure_is_logged_not_raised(tmp_path, monkeypatch):
    import api.anime as A
    monkeypatch.setattr(A, "_STACKS_CACHE_PATH", _unwritable(tmp_path))
    A._save_stacks_cache()
    assert obs.error_counts().get("anime") == 1


def test_al_cache_set_failure_is_logged_not_raised(tmp_path, monkeypatch):
    import api.anime as A
    monkeypatch.setattr(A, "_AL_CACHE_PATH", _unwritable(tmp_path))
    A._al_cache_set("tags", 42, {"x": 1})
    assert obs.error_counts().get("anime") == 1
