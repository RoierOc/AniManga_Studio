"""El estado del usuario no puede quedar a medias en disco.

`Path.write_text` trunca el fichero ANTES de escribir: entre la truncada y el último byte, en
disco hay un fichero vacío. Si ahí muere el proceso (cerrar WSL, parar el server, un corte de
luz), no se pierde la última escritura: se pierde el fichero ENTERO — y ahí viven el progreso de
lectura, el historial y la identidad canónica, que no se vuelven a descargar. Ya pasó una vez
(`media_progress` corrupto, revisión de julio de 2026).

Lo que se comprueba es la propiedad, no la implementación: tras un fallo A MITAD de la escritura,
lo que hay en disco sigue siendo el contenido viejo ENTERO. Y que un fichero ausente ("no había")
no se confunda con uno ilegible ("falló"), que es la regla cara del repo.

Correr:  .venv/bin/python -m pytest tests/test_atomic_state_writes.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.runtime import read_json_safe, write_json_atomic  # noqa: E402


def test_un_fallo_a_mitad_deja_el_fichero_VIEJO_entero(tmp_path, monkeypatch):
    p = tmp_path / "watch_history.json"
    write_json_atomic(p, [{"anime": "viejo"}])

    real_replace = os.replace

    def _muere_al_publicar(src, dst, *a, **kw):
        if str(dst) == str(p):
            raise OSError("simula un corte justo antes de publicar")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", _muere_al_publicar)
    with pytest.raises(OSError):
        write_json_atomic(p, [{"anime": "nuevo"}])

    assert json.loads(p.read_text()) == [{"anime": "viejo"}], \
        "tras un fallo a mitad debe quedar el contenido viejo COMPLETO, nunca uno truncado"
    assert not list(tmp_path.glob(".*tmp")), "el temporal fallido no puede quedarse de basura"


def test_no_deja_ventana_de_fichero_vacio(tmp_path):
    """La versión vieja es legible en TODO momento: nunca se trunca el destino."""
    p = tmp_path / "progress.json"
    write_json_atomic(p, {"ep": 1})
    inodo_viejo = p.stat().st_ino
    write_json_atomic(p, {"ep": 2})
    assert json.loads(p.read_text()) == {"ep": 2}
    assert p.stat().st_ino != inodo_viejo, \
        "publicar por rename crea un inodo nuevo; si se reescribe en sitio, hubo ventana vacía"


def test_el_respaldo_rescata_un_fichero_corrupto(tmp_path):
    p = tmp_path / "identity.json"
    write_json_atomic(p, {"al_id": 42}, keep_backup=True)
    write_json_atomic(p, {"al_id": 43}, keep_backup=True)

    p.write_text("{roto a medias")            # simula el daño ya ocurrido
    assert read_json_safe(p, default=None) == {"al_id": 42}, \
        "con el bueno roto se cae al último respaldo, no al default"


def test_ausente_y_corrupto_NO_son_lo_mismo(tmp_path):
    """La regla cara del repo: 'no había' ≠ 'falló'."""
    faltante = tmp_path / "no_existe.json"
    assert read_json_safe(faltante, default=[]) == []

    corrupto = tmp_path / "roto.json"
    corrupto.write_text("no soy json")
    registrados = []
    import api.observability as obs
    orig = obs.record_error
    obs.record_error = lambda comp, exc, **ctx: registrados.append(comp)
    try:
        assert read_json_safe(corrupto, default=[]) == []
    finally:
        obs.record_error = orig

    assert registrados, "un fichero ilegible SÍ se registra; uno ausente no"


def test_escribe_utf8_sin_escapar(tmp_path):
    p = tmp_path / "t.json"
    write_json_atomic(p, {"t": "終わり"})
    assert "終わり" in p.read_text(encoding="utf-8")


def test_dos_hilos_escribiendo_a_la_vez_no_se_pisan(tmp_path):
    """Waitress sirve con 8 hilos: `atómico entre procesos` no basta.

    El temporal se llamaba `.fichero.<pid>.tmp` — el MISMO nombre para todos los hilos del
    proceso. Dos peticiones concurrentes escribían encima del temporal del otro, uno hacía
    `os.replace` y al segundo le estallaba FileNotFoundError (2 casos reales de
    `manga_progress.json` en el log de un solo día, y 87 de 240 al forzarlo aquí).
    """
    import threading

    p = tmp_path / "prog.json"
    fallos = []

    def escribe(i):
        for _ in range(40):
            try:
                write_json_atomic(p, {"i": i}, keep_backup=True)
            except Exception as e:      # noqa: BLE001 — el test es justo sobre qué escapa
                fallos.append(repr(e))

    hilos = [threading.Thread(target=escribe, args=(i,)) for i in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not fallos, f"escrituras concurrentes perdidas: {fallos[:3]}"
    assert json.loads(p.read_text(encoding="utf-8"))["i"] in range(6)
    assert not list(tmp_path.glob(".prog.json.*.tmp")), "temporales huérfanos"
