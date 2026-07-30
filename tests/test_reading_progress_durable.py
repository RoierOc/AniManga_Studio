"""El progreso de lectura de manga: fundir sin destruir, y olvidar cuando se pide.

Qué había: el mapa de capítulos leídos de TODA la biblioteca y la página exacta de cada obra
vivían sólo en el localStorage del WebView. Ni la copia semanal al repo ni "Recuperar biblioteca"
lo traían, y el historial no lo reconstruye (son los últimos 500 capítulos terminados, no el mapa
por obra). Limpiar el almacenamiento del navegador lo borraba entero.

Las dos trampas del arreglo, que son las que se fijan aquí:

1. **Fundir, no sustituir.** Si el cliente pisara el fichero, tener la app abierta en dos sitios
   —o restaurar en una máquina nueva— borraría lo leído en la otra. Los leídos se UNEN; la
   posición la manda el `ts` más reciente (o al reanudar volverías a un punto viejo).
2. **Un borrado es una intención, no una ausencia.** Con fusión por unión, desmarcar un capítulo
   o borrar una obra "volvería" en la siguiente sincronización si no se declara aparte. Eso es
   exactamente lo que hace que la gente deje de fiarse de un sync.

Correr:  .venv/bin/python -m pytest tests/test_reading_progress_durable.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import api.reader as R  # noqa: E402
from api.reader import merge_progress  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from flask import Flask
    monkeypatch.setattr(R, "manga_dir", lambda: tmp_path)
    app = Flask(__name__)
    app.register_blueprint(R.reader_bp, url_prefix="/api/reader")
    return app.test_client()


# ── la fusión ────────────────────────────────────────────────────────────────

def test_los_leidos_se_UNEN_nunca_se_pierden():
    a = {"Obra": {"read": {"1": True, "2": True}, "ts": 10}}
    b = {"Obra": {"read": {"3": True}, "ts": 20}}
    assert merge_progress(a, b)["Obra"]["read"] == {"1": True, "2": True, "3": True}


def test_la_posicion_la_manda_el_mas_RECIENTE():
    viejo = {"Obra": {"read": {}, "lastChapter": "4", "lastPage": 9, "ts": 100}}
    nuevo = {"Obra": {"read": {}, "lastChapter": "7", "lastPage": 2, "ts": 200}}
    assert merge_progress(viejo, nuevo)["Obra"]["lastChapter"] == "7"
    # y al revés: lo viejo NO pisa lo nuevo
    assert merge_progress(nuevo, viejo)["Obra"]["lastChapter"] == "7"


def test_una_obra_que_solo_esta_en_un_lado_sobrevive():
    m = merge_progress({"Sólo aquí": {"read": {"1": True}}}, {"Sólo allí": {"read": {"5": True}}})
    assert set(m) == {"Sólo aquí", "Sólo allí"}


def test_la_fusion_no_muta_lo_que_recibe():
    a = {"Obra": {"read": {"1": True}, "ts": 1}}
    merge_progress(a, {"Obra": {"read": {"2": True}, "ts": 2}})
    assert a["Obra"]["read"] == {"1": True}, "el original no se toca: se devuelve uno nuevo"


def test_basura_en_el_fichero_no_tumba_la_fusion():
    assert merge_progress({"Obra": "no soy un dict"}, {"Obra": {"read": {"1": True}}})["Obra"]["read"]
    assert merge_progress({}, {"Obra": None}) == {}


# ── el endpoint ──────────────────────────────────────────────────────────────

def test_guardar_dos_veces_acumula(client):
    client.post("/api/reader/progress", json={"Obra": {"read": {"1": True}, "lastChapter": "1", "ts": 1}})
    r = client.post("/api/reader/progress", json={"Obra": {"read": {"2": True}, "lastChapter": "2", "ts": 2}})
    d = r.get_json()["Obra"]
    assert d["read"] == {"1": True, "2": True} and d["lastChapter"] == "2"
    assert client.get("/api/reader/progress").get_json() == r.get_json()


def test_sin_fichero_es_vacio_no_error(client):
    r = client.get("/api/reader/progress")
    assert r.status_code == 200 and r.get_json() == {}


def test_un_cuerpo_que_no_es_un_objeto_se_rechaza(client):
    assert client.post("/api/reader/progress", json=["no", "soy", "un", "objeto"]).status_code == 400


# ── los olvidos ──────────────────────────────────────────────────────────────

def test_desmarcar_un_capitulo_NO_lo_resucita(client):
    client.post("/api/reader/progress", json={"Obra": {"read": {"1": True, "2": True}, "ts": 1}})
    client.post("/api/reader/progress/forget", json={"chapters": {"Obra": ["2"]}})
    # el cliente vuelve a sincronizar SIN el capítulo desmarcado
    d = client.post("/api/reader/progress", json={"Obra": {"read": {"1": True}, "ts": 2}}).get_json()
    assert d["Obra"]["read"] == {"1": True}, "sin declarar el olvido, la unión lo volvería a marcar"


def test_olvidar_el_capitulo_por_el_que_ibas_suelta_la_posicion(client):
    client.post("/api/reader/progress",
                json={"Obra": {"read": {"5": True}, "lastChapter": "5", "lastPage": 3, "ts": 1}})
    client.post("/api/reader/progress/forget", json={"chapters": {"Obra": ["5"]}})
    d = client.get("/api/reader/progress").get_json()["Obra"]
    assert "lastChapter" not in d and "lastPage" not in d, \
        "reanudar un capítulo borrado no lleva a ninguna parte"


def test_borrar_una_obra_la_borra_entera(client):
    client.post("/api/reader/progress", json={"A": {"read": {"1": True}}, "B": {"read": {"1": True}}})
    client.post("/api/reader/progress/forget", json={"titles": ["A"]})
    assert set(client.get("/api/reader/progress").get_json()) == {"B"}


def test_el_fichero_queda_bien_formado_en_disco(client, tmp_path):
    client.post("/api/reader/progress", json={"Obra": {"read": {"1": True}, "ts": 1}})
    escrito = json.loads((tmp_path / "manga_progress.json").read_text(encoding="utf-8"))
    assert escrito["Obra"]["read"] == {"1": True}
