"""Fase 2 — fija las costuras de DECISIÓN del trasplante (transplant_core), las que degradan
la traducción EN SILENCIO si alguien las refactoriza y nadie mira un tomo entero.

Por qué NO medimos calidad de trasplante aquí:
  · el trasplante real necesita el detector RT-DETR (transformers+torch, ~0,7 s/página,
    descarga de HuggingFace) y un corpus de imágenes reales (binarios pesados) → no cabe en CI;
  · la calidad se mide a mano con el banco `data/_qa_translate/` (corpus real, aislado).

Lo que sí se puede fijar barato y sin modelo son las funciones GEOMÉTRICAS/de matching y los
GUARDARRAÍLES que aíslan la decisión ("¿este warp es fiable?", "¿estos dos globos son el mismo?").
Un cambio que las rompa no lanza ninguna excepción: simplemente empareja mal o tira texto bueno,
y no se nota hasta ver un capítulo raro semanas después — el patrón de fallo caro del proyecto.

`transplant_core` importa numpy/cv2/torch al cargarse; el CI de backend es LIGERO (solo ruff+pytest,
ver .github/workflows/ci.yml), así que estos tests se SALTAN en CI y corren en el suite local
(el `.venv` del proyecto, que es donde se toca el trasplante). importorskip lo deja explícito.

Correr:  .venv/bin/python -m pytest tests/test_transplant_decisions.py -q
"""
import os
import sys

import pytest

# Sin estas dependencias (CI ligero) el módulo entero se salta, no falla.
pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("torch")

import numpy as np  # noqa: E402
import cv2  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from transplant_core import (  # noqa: E402
    box_iou, edge_gap, dedup, match_hungarian, homography_unreliable, align,
    PAGE_ART_MAE_MAX,
)


# ── box_iou: la unidad con la que se deduplican y verifican solapes ────────────
def test_box_iou_identical_is_one():
    assert box_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0


def test_box_iou_disjoint_is_zero():
    assert box_iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_box_iou_half_overlap():
    # Solapan en la mitad de área: inter=50, union=150 → 1/3.
    assert box_iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 / 3, abs=1e-6)


# ── dedup: el detector emite cajas casi idénticas que roban emparejamientos ────
def test_dedup_collapses_near_duplicates_keeps_distinct():
    kept = dedup([[0, 0, 10, 10], [0, 0, 10, 11], [50, 50, 60, 60]])
    # Los dos casi-iguales colapsan en UNO; el lejano se conserva.
    assert len(kept) == 2
    assert [50, 50, 60, 60] in kept


def test_dedup_keeps_all_when_below_threshold():
    boxes = [[0, 0, 10, 10], [100, 100, 110, 110]]
    assert len(dedup(boxes)) == 2


# ── edge_gap: adyacencia para agrupar texto libre (invariante al tamaño) ───────
def test_edge_gap_zero_when_overlapping():
    assert edge_gap([0, 0, 10, 10], [5, 5, 15, 15]) == 0.0


def test_edge_gap_invariant_to_box_size():
    """El invariante documentado: dos captions del MISMO texto re-tipografiado (una caja alta,
    otra ancha) que se tocan tienen gap ~0 aunque sus CENTROIDES se separen mucho — por eso la
    agrupación usa gap de borde y no distancia de centroide."""
    tall, wide = [0, 0, 10, 100], [0, 0, 100, 10]
    # Centroides muy separados...
    c_tall, c_wide = (5, 50), (50, 5)
    centroid_dist = ((c_tall[0] - c_wide[0]) ** 2 + (c_tall[1] - c_wide[1]) ** 2) ** 0.5
    assert centroid_dist > 60
    # ...pero se tocan en la esquina → gap de borde 0.
    assert edge_gap(tall, wide) == 0.0


# ── match_hungarian: el emparejado 1:1 ES↔EN (el árbitro del trasplante) ───────
def test_match_hungarian_pairs_nearest_within_gate():
    en = [[0, 0, 10, 10], [100, 100, 110, 110]]
    es = [[1, 1, 11, 11], [300, 300, 310, 310]]
    pairs = match_hungarian(en, es, gate=50)
    # Solo el par cercano cae dentro del gate; el lejano (>gate) se descarta.
    assert [(int(i), int(j)) for i, j in pairs] == [(0, 0)]


def test_match_hungarian_empty_inputs():
    assert match_hungarian([], [[0, 0, 1, 1]], gate=10) == []
    assert match_hungarian([[0, 0, 1, 1]], [], gate=10) == []


# ── homography_unreliable: el gate que decide si pegar texto libre ─────────────
def _flat(shape, val):
    return np.full(shape, val, np.uint8)


def test_homography_unreliable_passes_aligned_page():
    en = _flat((200, 200, 3), 30)
    es_w = en.copy()  # arte perfectamente alineado
    H = np.eye(3, dtype=np.float32)
    empty = {0: [], 1: [], 2: []}
    unreliable, art_mae = homography_unreliable(en, es_w, empty, empty, H)
    assert unreliable is False
    assert art_mae == pytest.approx(0.0)


def test_homography_unreliable_flags_misaligned_page():
    en = _flat((200, 200, 3), 30)
    es_w = _flat((200, 200, 3), 210)  # arte totalmente desalineado (MAE ~180 > umbral)
    H = np.eye(3, dtype=np.float32)
    empty = {0: [], 1: [], 2: []}
    unreliable, art_mae = homography_unreliable(en, es_w, empty, empty, H)
    assert unreliable is True
    assert art_mae > PAGE_ART_MAE_MAX


def test_homography_unreliable_is_none_when_unmeasurable_not_a_crash():
    """Formas distintas → no hay arte comparable → art_mae None. NO debe reventar ni concluir
    'warp malo' (eso tiraría texto de una página que quizá está bien): 'no medible' ≠ 'malo'."""
    en = _flat((200, 200, 3), 30)
    es_w = _flat((100, 100, 3), 30)
    H = np.eye(3, dtype=np.float32)
    empty = {0: [], 1: [], 2: []}
    unreliable, art_mae = homography_unreliable(en, es_w, empty, empty, H)
    assert art_mae is None
    assert unreliable is False


# ── align: casa las páginas ES↔EN por dHash aunque una cara tenga páginas de más ─
def _page(tmp_path, name, seed):
    r = np.random.default_rng(seed)
    img = r.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    p = tmp_path / name
    cv2.imwrite(str(p), img)
    return str(p)


def test_align_identity_when_pages_match(tmp_path):
    en = [_page(tmp_path, f'en{i}.png', i) for i in range(3)]
    es = [_page(tmp_path, f'es{i}.png', i) for i in range(3)]  # mismos seeds → mismo dHash
    assert align(es, en) == {0: 0, 1: 1, 2: 2}


def test_align_skips_duplicate_es_page(tmp_path):
    """Si la cara ES trae una página duplicada de más, cada página EN debe seguir mapeando a SU
    página ES — no correrse una posición (que traduciría con el texto equivocado)."""
    en = [_page(tmp_path, f'en{i}.png', i) for i in range(3)]
    es = [_page(tmp_path, f'es{i}.png', i) for i in range(3)]
    es_with_dup = [es[0], es[0], es[1], es[2]]  # ES: A A B C ; EN: A B C
    m = align(es_with_dup, en)
    assert m[0] == 0        # EN0 → primera A
    assert m[1] == 2        # EN1 → B (índice 2 en la lista ES con duplicado)
    assert m[2] == 3        # EN2 → C
