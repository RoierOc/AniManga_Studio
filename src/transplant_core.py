#!/usr/bin/env python3
"""
transplant_core — trasplante de texto ES sobre arte EN, capítulo completo.

Port directo y FIEL del spike validado en
`/Manga_Upscaler_project/transplant_spike/run_chapter.py`. La lógica de visión
(homografía por similaridad, globos precisos con Hungarian+ECC+IoU de forma +
asimetría de clase, texto libre con guardas, alineación dHash+DTW, fallback a
página ES completa) se conserva INTACTA. Aquí sólo cambia el envoltorio:

  - el modelo se carga lazy (primera llamada) en CPU, para no competir con el
    worker GPU de upscale;
  - no hay `sys.argv`/`main()`: se expone `transplant_chapter(...)` importable;
  - la salida se escribe con el esquema de nombres del app `{prefix}_{i:03d}.png`.

Uso:
    from transplant_core import transplant_chapter
    stats = transplant_chapter(es_dir, en_dir, out_dir, file_prefix="ch0036")
"""
from pathlib import Path
import json
import numpy as np, cv2, torch
from PIL import Image
from scipy.optimize import linear_sum_assignment

# ── Modelo (carga lazy, CPU) ──────────────────────────────────────────────────
_proc = None
_model = None


def _ensure_model():
    """Carga el detector RT-DETR una sola vez, en CPU. ~0.7s/pág de inferencia."""
    global _proc, _model
    if _model is None:
        from transformers import AutoModelForObjectDetection, AutoImageProcessor
        _proc = AutoImageProcessor.from_pretrained("ogkalu/comic-text-and-bubble-detector")
        _model = AutoModelForObjectDetection.from_pretrained(
            "ogkalu/comic-text-and-bubble-detector").eval()
    return _proc, _model


IOU_MIN = 0.50           # verificación "mismo globo" por FORMA del interior (idioma-independiente)
MAX_ECC_DRIFT = 6.0      # px: la homografía global ya es sub-píxel; un ECC que derive más
                         # que esto es un mal-anclaje local (cajas planas) -> se descarta
MATCH_GATE_FRAC = 0.05   # coste máx de match globo = 5% del alto de página
TOL_FRAC = 0.06          # tolerancia de búsqueda de texto libre = 6% del alto (legacy, ver text_free)
TEXTFREE_GAP_FRAC = 0.055  # ADYACENCIA de borde (no centroide) para agrupar captions EN<->ES.
                           # Los captions ES se re-tipografían en otra posición/desglose que los EN
                           # (otra longitud -> otro salto de línea -> otro bbox): el match 1:1 por
                           # CENTROIDE perdía ~38% (offset>tol) y fragmentaba los de varias piezas.
                           # Medido en cap 2+3: el 62% de las cajas perdidas SOLAPA un candidato ES
                           # (gap=0) y hay un valle natural en la distribución de gaps entre ~112px y
                           # ~181px; 5.5% del alto cae en ese valle (capta los adyacentes reales,
                           # excluye los lejanos = SFX/títulos/maquetado distinto).
TEXTFREE_CLUSTER_MAXAREA = 0.35  # un cluster cuyo bbox supere el 35% del área de página es casi
                                 # seguro un sobre-merge -> se intenta PARTIR (ver TEXTFREE_SPLIT_*)
                                 # y, si no se puede, no se pega (lo cubren globos/fallback)
# Partición adaptativa del sobre-merge: al pasarse de MAXAREA, se re-agrupa el cluster con el gap
# decaído hasta que las piezas quepan. El decay es geométrico (no un gap "correcto" por página:
# no existe — depende de cómo reparta el autor los captions), y el suelo evita partir un caption
# de varias líneas en líneas sueltas (el interlineado ronda el 1-2% del alto).
TEXTFREE_SPLIT_DECAY = 0.6
TEXTFREE_SPLIT_MINGAP = 0.012   # frac. del alto de página
TEXTFREE_SPLIT_MAXDEPTH = 5
TEXTFREE_LARGE_FRAC = 0.12  # un cluster de texto libre que cubra >12% de la página pega mucho FONDO;
                            # si la homografía está algo corrida (página de baja textura / fuente ES
                            # de mucha menor resolución), el borde del paste deja una COSTURA visible
                            # (líneas/tramado que no casan). Para esos se EXIGE confirmación de ECC
                            # ajustado (igual que el fallback de globos); si el ECC no confirma, NO se
                            # pega -> mejor el caption en inglés limpio que una costura. Los clusters
                            # pequeños no lo necesitan (la costura sería imperceptible). Medido: p007
                            # HDWR (costura) ECC=None NCC=0.12; p026 (oscura, OK) ECC=0.44 NCC=0.53.
FALLBACK_BUBBLE_MIN_CONF = 0.50  # sólo recuperar globos sueltos si la homografía de la
                                 # página es confiable (>=50% de globos ya emparejados)
# ── Homografía: matchear a RESOLUCIÓN COMÚN + validar la similaridad estimada ──
# CAUSA RAÍZ (medida en QA How Do We Relationship cap10 p004): el arte EN local es HD (3840px) y
# la fuente ES (Mangas.in) viene a 960px = ¼ de resolución. ORB calcula descriptores a la
# resolución NATIVA de cada imagen; con 4× de diferencia los descriptores son incomparables ->
# cientos de matches ESPURIOS y RANSAC colapsa (medido: 4 inliers/573, escala 0.877, rot 153°)
# -> warp basura -> es_w rotado -> globos sin componer (inglés) + parche ES torcido sobre el arte.
# FIX: se reescalan AMBAS imágenes a un lado mayor común (HOMOG_WORK) ANTES de ORB; en ese espacio
# son la misma página a la misma escala -> matches limpios (medido: 1316 inliers/1706, escala 1.000,
# rot 0.00°) -> alineación perfecta. El transform se compone de vuelta a coords de la ES original.
HOMOG_WORK = 1500         # lado mayor del espacio de trabajo común (más inliers que 2000/2500;
                          # además ORB en 1500px es más rápido que en el EN nativo de 3840px).
# Dos escaneos de la MISMA página, ya igualados en resolución, difieren sólo por similaridad
# casi-identidad (rotación ~0°, ESCALA ~1.0 en el espacio de trabajo). Se rechaza la matriz si:
HOMOG_MIN_INLIERS = 8     # piso de degeneración: por debajo el ajuste no es fiable (con res común
                          # se obtienen cientos de inliers en páginas reales, así que sólo caza basura).
HOMOG_MAX_ROT_DEG = 10.0  # dos escaneos de la misma página no rotan; >10° = warp roto.
HOMOG_SCALE_LO = 0.6      # banda de escala en el ESPACIO DE TRABAJO (esperada ~1.0 porque ambas
HOMOG_SCALE_HI = 1.7      # imágenes se normalizaron al mismo lado mayor antes de matchear).
# TEXTFREE_MIN_BUBBLES = 2 — RETIRADA 2026-07-15 junto al proxy de globos del gate de texto libre.
# Existía para no juzgar la homografía con menos de 2 globos; con la medida DIRECTA del arte
# (page_art_mae_masked) no hacen falta globos para juzgar el warp. Ver PAGE_ART_MAE_MAX.
COVERAGE_MIN = 0.50       # umbral para ETIQUETAR la página como "trasplante parcial" (reason
                          # low-coverage). OJO: NO dispara respaldo — el código devuelve el
                          # resultado PARCIAL a propósito (ver text_boxes/cov abajo): arte EN
                          # bueno con texto a medias > arte ES malo. El comentario anterior decía
                          # "usar la página ES completa" y era FALSO desde hace tiempo.
# ── Guarda `bg` de TEXTO LIBRE — RETIRADA 2026-07-15 (A/B, 26 caps / 716 págs) ───────────────
# Eran TEXTFREE_BG_MAXDIFF (30) + TEXTFREE_DARK_LUMA (128) + TEXTFREE_BG_RING_MAE (22): si el
# brillo dentro de la caja no cuadraba entre EN y ES, el cluster NO se pegaba y el caption se
# quedaba en INGLÉS. NO reintroducir, NI "recalibrada": el fallo es de MAGNITUD, no de valor.
#  1) bg_consistent mide DENTRO de la caja, donde conviven el texto (que DEBE diferir: es justo
#     lo que se sustituye) y la curva de tono del scan. Mismo error conceptual que la guarda de
#     TINTA ya retirada (ver ONLYEN_INK_ADD_MAX abajo): dos guardas independientes, un único
#     error — juzgar dentro de la caja lo que sólo puede juzgarse fuera.
#  2) Medido: retirarla da 642 -> 654 págs al 100% (+12), los 22 descartes `bg` bajan a 0, con
#     19 páginas mejores y 0 peores, en AMBOS títulos. En los 22 el ES YA estaba detectado y
#     traducido (n_es>0 en 22/22): la guarda tiraba traducción correcta, no basura.
#  3) Recalibrar en vez de retirar es PEOR y está medido: subir sólo el indulto del anillo a 60
#     deja 652 y 3 descartes -> la constante no es el problema.
#  4) OJO: no vale "ascender el anillo a guarda universal" (probado: ringonly26 -> 614 págs,
#     ringonly60 -> 627, AMBOS PEOR que no tener nada). El anillo asume que fuera de la caja no
#     hay texto: cierto en un GLOBO, falso en TEXTO LIBRE, donde el ES se re-tipografía más largo
#     y se derrama hasta el anillo -> rechaza pegados correctos.
# Qué protege ahora: art_not_erased (guarda de ESTRUCTURA, independiente, sigue cazando sus casos)
# y, a nivel de PÁGINA, la alineación (ver COVERAGE_MIN / es_fullpage). bg_consistent SIGUE VIVA
# en precise_bubbles(), donde sí es válida: un globo es blanco plano sobre arte y ahí el brillo
# es señal de verdad (medianDiff 30-233), no ruido de tipografía.
TEXTFREE_ART_MAXEXTRA = 30  # umbral de std para texto libre: un scan ES más BLANDO (menos
                            # contraste) que el EN da std_diff +12..27 con el MISMO arte
                            # (medianDiff~0). Con 11 se rechazaban captions reales; los pegados
                            # malos (blanco sobre arte) los caza bg_consistent (medianDiff 30-233).
# ── Texto libre SOLO-EN (SFX sobre arte) ─────────────────────────────────────
# Un cluster de texto EN sin ES emparejado casi siempre es un SFX/caption que el scan ES
# BORRÓ (redibujó el arte limpio) o RE-TIPOGRAFIÓ en otro sitio (y otro cluster ya lo pegó).
# Antes se omitía siempre -> el inglés se quedaba SOBRE EL ARTE (queja recurrente). Ahora se
# pega la región ES (= converger al release ES) sólo con pruebas GEOMÉTRICAS fuertes:
ONLYEN_MAX_FRAC = 0.06      # área máx del cluster (frac. de página): los SFX son pequeños; un
                            # cluster grande sin ES es sospechoso -> no arriesgar media página.
ONLYEN_RING_PAD = 10        # grosor del anillo de arte examinado alrededor de la caja (px)
ONLYEN_RING_MAE = 26        # MAE máx del anillo EN vs ES alineado: prueba "es el MISMO dibujo y
                            # está BIEN alineado aquí". Es la guarda clave — sustituye a
                            # bg_consistent/art_not_erased, que aquí no valen: ambas comparan
                            # DENTRO de la caja, donde el EN tiene tinta (texto) y el ES no, así
                            # que rechazaban justo el caso que queremos arreglar.
# ONLYEN_INK_ADD_MAX = 1.15 — RETIRADO 2026-07-14 (QA masivo, 26 caps / 716 págs).
# NO reintroducir, NI SIQUIERA "recalibrado": el problema no es el valor, es la MAGNITUD.
# Vetaba pegar si la región ES tenía >15% más tinta que la EN, asumiendo que eso delataba otro arte.
# Falla por dos motivos:
#  1) Mide DENTRO de la caja, donde el texto difiere POR DEFINICIÓN (es justo lo que se va a
#     sustituir). Mismo error conceptual que la guarda `bg` (retirada 2026-07-15, ver arriba).
#  2) La tinta depende de la TIPOGRAFÍA del scan, que NO es constante en ninguna dirección.
#     Medido sobre 49 clústeres solo-EN: ratio ES/EN de **0.17 a 7.47** (44x de rango, continuo, sin
#     valle) — el ES es MÁS FINO en el 24% de los casos y MÁS NEGRITA en el 65%. Un SFX que el scan
#     borró da 0.17; un bloque de diálogo renegrido da 7.47. NINGÚN umbral separa "mismo arte" de
#     "arte distinto" aquí: la tinta no es señal, es ruido.
# Coste real que tenía: era el modo de fallo DOMINANTE (30 de 48 descartes "only-en"); de esos, 24
# (80%) tenían el ANILLO casando (MAE mediana 11.5) → se tiraba la traducción ES CORRECTA y bien
# alineada ("KANON, ¿ESTÁS DESPIERTA?") por el grosor de la fuente.
# El árbitro es el ANILLO: mira FUERA de la caja, donde el texto no lo contamina.


def load_rgb(p): return cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
def dhash(g, hs=16): g = cv2.resize(g, (hs+1, hs)); return (g[:, 1:] > g[:, :-1]).flatten()

def _is_color_page(rgb):
    """True si >10% de píxeles tienen diferencia R/G/B > 15 (portada/splash en color).
    Mismo criterio que upscale.py / transplant.py:_is_color_bytes."""
    arr = rgb.astype(np.float32)
    max_diff = arr.max(axis=2) - arr.min(axis=2)
    return float(np.mean(max_diff > 15)) > 0.10
def cxcy(b): return ((b[0]+b[2])/2.0, (b[1]+b[3])/2.0)
def area(b): return max(1, (b[2]-b[0])*(b[3]-b[1]))


def box_iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1-ix0) * max(0, iy1-iy0)
    return inter / max(1, area(a) + area(b) - inter)


def edge_gap(a, b):
    """Distancia mínima borde-a-borde entre dos cajas (0 si solapan). A diferencia de la
    distancia de centroide, no crece con el tamaño de la caja: dos captions del MISMO texto
    re-tipografiado (uno más alto/ancho que el otro) siguen teniendo gap ~0 aunque sus
    centroides se separen mucho. Es la métrica de adyacencia para agrupar texto libre."""
    dx = max(a[0]-b[2], b[0]-a[2], 0.0)
    dy = max(a[1]-b[3], b[1]-a[3], 0.0)
    return (dx*dx + dy*dy) ** 0.5


def dedup(boxes, thr=0.6):
    """El detector emite cajas casi idénticas (IoU~0.99) para un mismo globo;
    compiten en el matching 1:1 y roban emparejamientos. Fusiona los duplicados."""
    boxes = sorted(boxes, key=lambda b: -area(b)); keep = []
    for b in boxes:
        if all(box_iou(b, k) < thr for k in keep): keep.append(b)
    return keep


def detect(rgb):
    proc, model = _ensure_model()
    pil = Image.fromarray(rgb)
    with torch.no_grad():
        out = model(**proc(images=pil, return_tensors="pt"))
    d = proc.post_process_object_detection(out, target_sizes=torch.tensor([[pil.height, pil.width]]), threshold=0.30)[0]
    res = {0: [], 1: [], 2: []}
    for l, b in zip(d["labels"], d["boxes"]): res[int(l)].append([int(v) for v in b])
    return res


def homography(es, en):
    eg, ng = cv2.cvtColor(es, cv2.COLOR_RGB2GRAY), cv2.cvtColor(en, cv2.COLOR_RGB2GRAY)
    # RESOLUCIÓN COMÚN antes de ORB: si ES y EN difieren mucho de tamaño (ES de baja res vs arte
    # local HD), los descriptores nativos son incomparables y RANSAC colapsa (ver HOMOG_WORK).
    # Se reescala cada gris a un lado mayor = HOMOG_WORK y se matchea ahí; luego se compone el
    # transform de vuelta a las coords de la ES ORIGINAL (que es la que se warpea/mapea después).
    s_es = HOMOG_WORK / max(eg.shape)
    s_en = HOMOG_WORK / max(ng.shape)
    egw = cv2.resize(eg, None, fx=s_es, fy=s_es, interpolation=cv2.INTER_AREA if s_es < 1 else cv2.INTER_CUBIC)
    ngw = cv2.resize(ng, None, fx=s_en, fy=s_en, interpolation=cv2.INTER_AREA if s_en < 1 else cv2.INTER_CUBIC)
    orb = cv2.ORB_create(4000)
    k1, d1 = orb.detectAndCompute(egw, None); k2, d2 = orb.detectAndCompute(ngw, None)
    if d1 is None or d2 is None: return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
    if len(matches) < 12: return None
    src = np.float32([k1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    # SIMILARIDAD (traslación + escala + rotación), NO homografía de 8 DOF. Dos scans de
    # la MISMA página difieren sólo por similaridad; una homografía completa SOBREAJUSTA
    # rotaciones/perspectivas espurias cuando hay pocos inliers (p021: 15 inliers -> rot
    # falsa de -2.9° -> texto de las cajas planas cortado/torcido, porque el ECC no las
    # corrige). El modelo restringido da muchos más inliers (69) y rotación correcta ~0°.
    Mw, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0)
    if Mw is None: return None
    # Validar en el ESPACIO DE TRABAJO (escala esperada ~1.0, rot ~0°). Si no es plausible el warp
    # está roto: rechazar -> la página cae a "arte EN limpio" en vez de a un parche torcido.
    n_in = int(inliers.sum()) if inliers is not None else 0
    a, b = float(Mw[0, 0]), float(Mw[1, 0])
    scale = (a * a + b * b) ** 0.5
    rot = abs(np.degrees(np.arctan2(b, a)))
    if (n_in < HOMOG_MIN_INLIERS or rot > HOMOG_MAX_ROT_DEG
            or not (HOMOG_SCALE_LO <= scale <= HOMOG_SCALE_HI)):
        return None
    # Componer de vuelta a coords ORIGINALES: es_orig --(×s_es)--> trabajo --(Mw)--> en_trabajo
    # --(÷s_en)--> en_orig.  H = S_en⁻¹ · Mw · S_es   (mapea es_orig → en_orig, como esperan
    # warpPerspective(es, H, en_size) y map_box(H, caja_es)).
    Mw3 = np.vstack([Mw, [0.0, 0.0, 1.0]])
    S_es = np.diag([s_es, s_es, 1.0])
    S_en_inv = np.diag([1.0 / s_en, 1.0 / s_en, 1.0])
    return S_en_inv @ Mw3 @ S_es


# ── `text_bubble` HUÉRFANO — PROBADO Y DESCARTADO 2026-07-15 (A/B, 26 caps / 716 págs) ───────
# Idea (razonable, y FALSA): el detector da bubble(0) / text_bubble(1) / text_free(2), y el
# pipeline sólo consume la 0 (precise_bubbles -> es_leftover) y la 2 (text_free). Si el detector
# marca el TEXTO (1) pero falla el GLOBO (0) que lo envuelve, esa caja no está en ds[0] ni en
# ds[2]. Parecía un agujero: medido en ch0044_031 y ch0045_013, el ES estaba ahí como cls1 con
# cls0=0 dentro del bbox. Se probó a inyectar esas huérfanas como candidatas de texto libre.
# NO REPETIRLO. Resultado (brazo `orphan_es`, aislado para no tocar el denominador de `coverage`):
# **0 páginas mejores / 3 PEORES**; con ambos lados, 654 -> 652 y `only-en:ring` intacto en 22.
# Por qué falla, y es la lección: **el diseño YA cubre ese caso**. `_erase_onlyen` pega la REGIÓN
# ES ENTERA, que CONTIENE el texto español (su docstring lo dice: caso "b"). Detectar la caja no
# aporta nada, y encima daña: con n_es>0 el cluster abandona la ruta de borrado (que funcionaba)
# por la ruta normal, y las cajas nuevas RE-AGRUPAN el clustering de toda la página.
# Que ch0044_031 quede en inglés NO es invisibilidad: es el ANILLO rechazando (MAE 26.7 vs 26).
# Es un caso `only-en:ring`, que es otro problema.


PAGE_ART_MAE_MAX = 55       # MAE del ARTE de la página (texto enmascarado) por encima del cual el
                            # warp NO es de fiar -> no se pega nada. Medido sobre las 716 págs del
                            # banco: masa continua en 0-45 (p99=35.9), **hueco VACÍO entre 45 y 67**
                            # y luego 3 págs (67.4, 95.7, 109.9) verificadas ROTAS a ojo (arte
                            # fantasmeado en solape de falso color). 55 cae en el centro del hueco:
                            # es una frontera real, no un dial. Enmascarar el texto es obligatorio:
                            # sin máscara una página DENSA da MAE alto con el dibujo perfecto
                            # (ch0038_029) — el texto EN y ES difieren POR DEFINICIÓN.
PAGE_MASK_PAD = 6           # px de holgura al enmascarar cada caja de texto


def page_art_mae_masked(en, es_w, de, ds, H):
    """MAE del ARTE (texto enmascarado) entre `en` y la ES ya warpeada. None si no es medible.

    Es la medida DIRECTA de "¿está bien alineado este warp?". Sustituye al proxy que había antes
    (la TASA DE GLOBOS COMPUESTOS), que se rompía de forma silenciosa y cara: en una página cuyos
    globos son viñetas japonesas que NINGÚN scan traduce, ninguno se compone -> conf=0 -> el gate
    concluía "warp malo" y tiraba TODO el texto libre de una página perfectamente alineada, sin
    dejar rastro en las métricas (no registraba ni un skip).
    Medido: ch0041_013 (19 globos, 0 compuestos, conf 0.00 -> mataba 17 cajas ES) y ch0049_014
    (conf 0.31) tienen art_mae 24.2 y 24.6 = BUENAS. Las 3 rotas de verdad dan 67.4/95.7/109.9.
    El proxy fallaba 2 de 5; la medida directa acierta las 5.
    Barato: `en`, `es_w`, `de`, `ds` y `H` ya están calculados en _transplant_page."""
    eg = cv2.cvtColor(en, cv2.COLOR_RGB2GRAY).astype(np.float32)
    sg = cv2.cvtColor(es_w, cv2.COLOR_RGB2GRAY).astype(np.float32)
    if eg.shape != sg.shape:
        return None
    valid = (sg < 250) | (eg < 250)          # fuera el relleno blanco del warp
    mask = np.ones(eg.shape, bool)
    for k in (0, 1, 2):                      # bubble / text_bubble / text_free, de AMBOS lados
        for b in de.get(k, []):
            mask[max(0, b[1]-PAGE_MASK_PAD):b[3]+PAGE_MASK_PAD,
                 max(0, b[0]-PAGE_MASK_PAD):b[2]+PAGE_MASK_PAD] = False
        for b in ds.get(k, []):
            m = map_box(H, b)
            mask[max(0, m[1]-PAGE_MASK_PAD):m[3]+PAGE_MASK_PAD,
                 max(0, m[0]-PAGE_MASK_PAD):m[2]+PAGE_MASK_PAD] = False
    sel = mask & valid
    if sel.sum() < 500:                      # sin arte que medir -> no se puede juzgar
        return None
    return float(np.abs(eg - sg)[sel].mean())


def homography_unreliable(en, es_w, de, ds, H, st=None):
    """¿Es el warp de esta página tan malo que NO se debe pegar texto libre? -> (bool, art_mae).

    Unidad con nombre a propósito: es UNA decisión, se mide sola y el banco puede sustituirla por
    la versión antigua para un A/B honesto (`ab_bg.py oldgate`). `st` se acepta y se ignora: lo
    usaba el proxy viejo (tasa de globos compuestos) y lo conserva la firma para poder compararlos."""
    art_mae = page_art_mae_masked(en, es_w, de, ds, H)
    return (art_mae is not None and art_mae > PAGE_ART_MAE_MAX), art_mae


def map_box(H, b):
    pts = np.float32([[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]).reshape(-1, 1, 2)
    mp = cv2.perspectiveTransform(pts, H).reshape(-1, 2)
    return [int(mp[:, 0].min()), int(mp[:, 1].min()), int(mp[:, 0].max()), int(mp[:, 1].max())]


def match_hungarian(en_boxes, es_boxes_mapped, gate):
    if not en_boxes or not es_boxes_mapped: return []
    C = np.zeros((len(en_boxes), len(es_boxes_mapped)))
    for i, eb in enumerate(en_boxes):
        ec = cxcy(eb); ea = area(eb)
        for j, sb in enumerate(es_boxes_mapped):
            sc = cxcy(sb)
            d = ((ec[0]-sc[0])**2 + (ec[1]-sc[1])**2) ** 0.5
            sz = abs(ea - area(sb)) / max(ea, area(sb))
            # El término de tamaño es sólo un desempate suave (la homografía distorsiona
            # el bounding-box y lo inflaba): la distancia de centro manda. Pares dudosos
            # los filtra después la verificación por IoU de forma.
            C[i, j] = d + sz * 20
    ri, cj = linear_sum_assignment(C)
    return [(i, j) for i, j in zip(ri, cj) if C[i, j] <= gate]


def _dominant_component(crop):
    """Componente uniforme dominante del interior de un globo, sea CLARO (globo blanco,
    texto negro) u OSCURO (globo negro/invertido, texto blanco). Devuelve (lab==big, frac)
    de la polaridad cuyo componente conexo central sea mayor. Esto permite traducir también
    los globos OSCUROS (antes sólo se detectaban los claros con `>=180`)."""
    best = None  # (area, labmask, frac)
    k = np.ones((5, 5), np.uint8)
    for mask in ((crop >= 180), (crop <= 75)):
        m = cv2.morphologyEx(mask.astype(np.uint8)*255, cv2.MORPH_CLOSE, k)
        num, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        if num <= 1:
            continue
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        area = int(stats[big, cv2.CC_STAT_AREA])
        if best is None or area > best[0]:
            best = (area, (lab == big), area / max(1, crop.shape[0]*crop.shape[1]))
    if best is None:
        return None, 0.0
    return best[1], best[2]


def interior_mask(rgb, box):
    x0, y0, x1, y1 = box; h, w = rgb.shape[:2]
    x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(w, x1), min(h, y1)
    if x1-x0 < 6 or y1-y0 < 6: return None, None
    crop = cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    comp, frac = _dominant_component(crop)
    if comp is None or frac < 0.05:
        # Globo con arte/sombreado interno (burbujas de pensamiento, fondos con degradado):
        # la región dominante no supera el 5 %. Usar rectángulo con inset como máscara;
        # el shape_iou y el ECC siguen protegiendo de composiciones incorrectas.
        ins = max(2, int(0.04 * min(x1-x0, y1-y0)))
        bh, bw = y1-y0, x1-x0
        mm = np.zeros((bh, bw), np.uint8)
        if ins * 2 < bh and ins * 2 < bw:
            mm[ins:bh-ins, ins:bw-ins] = 255
        else:
            mm[:] = 255
        return mm, (x0, y0, x1, y1)
    if frac >= 0.90:
        mm = np.zeros_like(crop); ins = max(2, int(0.04*min(crop.shape))); mm[ins:-ins or None, ins:-ins or None] = 255
    else:
        mm = comp.astype(np.uint8)*255
    ff = mm.copy(); fm = np.zeros((mm.shape[0]+2, mm.shape[1]+2), np.uint8)
    cv2.floodFill(ff, fm, (0, 0), 255); mm = mm | cv2.bitwise_not(ff)
    return mm, (x0, y0, x1, y1)


def interior_blob(gray_crop):
    """Forma del interior rellena (globo/caja): la firma del globo, igual en ES y EN
    (el texto de dentro NO influye). Polaridad-agnóstica: sirve para globos claros
    (interior blanco) y OSCUROS (interior negro). Para globos con arte interno sin región
    dominante, devuelve el rectángulo completo (shape_iou entre dos globos de arte = 1.0)."""
    if gray_crop.size == 0: return None
    comp, _ = _dominant_component(gray_crop)
    if comp is None:
        # Sin región dominante clara (arte/sombreado interno): usar todo el crop como firma.
        return np.ones(gray_crop.shape, np.uint8)
    mm = comp.astype(np.uint8)
    ff = mm.copy(); fm = np.zeros((mm.shape[0]+2, mm.shape[1]+2), np.uint8)
    cv2.floodFill(ff, fm, (0, 0), 1); return mm | (1 - ff)


def shape_iou(blob_a, blob_b):
    if blob_a is None or blob_b is None: return 0.0
    inter = np.count_nonzero(blob_a & blob_b)
    uni = np.count_nonzero(blob_a | blob_b)
    return inter / max(1, uni)


def ecc_refine(en_gray, es_gray):
    a = cv2.GaussianBlur(en_gray.astype(np.float32), (0, 0), 2)
    b = cv2.GaussianBlur(es_gray.astype(np.float32), (0, 0), 2)
    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-4)
    try:
        cv2.findTransformECC(a, b, warp, cv2.MOTION_EUCLIDEAN, crit, None, 5)
        return warp
    except cv2.error:
        return None


def bg_consistent(en, es_w, x0, y0, x1, y1, max_diff=20, blockwise=False, grid=4, min_frac=0.6):
    """¿Casa el fondo del ES warpeado con el del EN en la caja? Compara la mediana GLOBAL
    (rápido, para globos). `blockwise=True` (texto libre) AÑADE un respaldo: si la prueba
    global FALLA, reintenta por una rejilla grid×grid y pasa si una MAYORÍA (`min_frac`) de
    celdas casa con su tono LOCAL. Es un OR sobre la global → estrictamente MÁS permisivo,
    nunca rechaza lo que la global aceptaba (cero regresión), sólo recupera casos extra.

    Motivo: una caption que CRUZA un borde de alto contraste (p.ej. margen blanco de la
    página ↔ panel fotográfico oscuro) tiene medianas globales EN/ES que difieren sólo por
    cuánto tono claro/oscuro cae a cada lado tras una homografía sub-píxel imperfecta —no
    porque el fondo sea realmente distinto—, y la prueba global la rechazaba en falso. Por
    celdas, cada una se compara con su tono LOCAL (blanco con blanco, negro con negro), así
    que el straddle pasa; un paste de verdad desalineado (otro arte) falla la global Y la
    mayoría de celdas, y sigue rechazándose."""
    ae = cv2.cvtColor(en[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    be = cv2.cvtColor(es_w[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    if ae.size == 0: return False
    if abs(float(np.median(ae)) - float(np.median(be))) < max_diff:
        return True
    gh, gw = ae.shape
    if not blockwise or gh < grid * 2 or gw < grid * 2:
        return False
    ok = tot = 0
    for gy in range(grid):
        ya, yb = gy * gh // grid, (gy + 1) * gh // grid
        for gx in range(grid):
            xa, xb = gx * gw // grid, (gx + 1) * gw // grid
            ab, bb = ae[ya:yb, xa:xb], be[ya:yb, xa:xb]
            if ab.size == 0: continue
            tot += 1
            if abs(float(np.median(ab)) - float(np.median(bb))) < max_diff: ok += 1
    return tot > 0 and ok / tot >= min_frac


def _ink(gray):
    """Fracción de píxel 'con tinta' (oscuro) — proxy de cuánto texto/trazo hay."""
    if gray.size == 0: return 0.0
    return float(np.count_nonzero(gray < 128)) / gray.size


def _ring_mae(en_g, esw_g, x0, y0, x1, y1, pad=ONLYEN_RING_PAD):
    """¿El arte que RODEA la caja es el mismo en ambos escaneos y está alineado?
    Devuelve el MAE del anillo (None si no hay anillo medible); menor = mejor encaje.

    Compara sólo un ANILLO de `pad` px por FUERA del bbox: ahí no hay texto (el detector
    acotó el texto dentro), así que lo único que se compara es ARTE. Un anillo que casa
    demuestra dos cosas a la vez: (a) es el mismo dibujo —no una página/panel distinto— y
    (b) la homografía está fina EN ESTE PUNTO. Ambas son justo lo que hace falta para poder
    borrar con seguridad un texto EN sin contrapartida ES detectada, y de paso evita el
    'paste ligeramente corrido' (si estuviera corrido, el anillo no casaría)."""
    h, w = en_g.shape[:2]
    rx0, ry0 = max(0, x0 - pad), max(0, y0 - pad)
    rx1, ry1 = min(w, x1 + pad), min(h, y1 + pad)
    a = en_g[ry0:ry1, rx0:rx1].astype(np.float32)
    b = esw_g[ry0:ry1, rx0:rx1].astype(np.float32)
    if a.size == 0 or a.shape != b.shape: return None
    mask = np.ones(a.shape, bool)
    ix0, iy0 = x0 - rx0, y0 - ry0
    ix1, iy1 = ix0 + (x1 - x0), iy0 + (y1 - y0)
    mask[max(0, iy0):max(0, iy1), max(0, ix0):max(0, ix1)] = False   # excluye el interior
    if np.count_nonzero(mask) < 64: return None
    return float(np.mean(np.abs(a[mask] - b[mask])))


def art_not_erased(en, es_w, x0, y0, x1, y1, max_extra=11):
    ae = cv2.cvtColor(en[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    be = cv2.cvtColor(es_w[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    if ae.size == 0: return False
    f = 4
    a = cv2.resize(ae, (max(1, ae.shape[1]//f), max(1, ae.shape[0]//f)))
    b = cv2.resize(be, (max(1, be.shape[1]//f), max(1, be.shape[0]//f)))
    return (a.std() - b.std()) <= max_extra


def _compose_exact(res, en_shape, mm, coords, es_src):
    """Pega es_src sobre res usando máscara de CONTORNO EXACTO (open + erode 1px +
    feather σ=1) en la caja `coords`. mm = interior relleno del globo en coords-crop."""
    x0, y0, x1, y1 = coords
    alpha = np.zeros(en_shape[:2], np.float32)
    ell = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mm2 = cv2.morphologyEx(mm, cv2.MORPH_OPEN, ell)
    mm2 = cv2.erode(mm2, ell, iterations=1)
    sub = cv2.GaussianBlur(mm2.astype(np.float32), (0, 0), 1.0)/255.0
    alpha[y0:y1, x0:x1] = sub
    a = alpha[:, :, None]
    return es_src.astype(np.float32) * a + res * (1 - a)


def _aligned_es_region(en_g, esw_g, es_w, coords):
    """Para el globo en `coords`, devuelve (es_full, dxdy) donde es_full = es_w con la
    región del globo refinada por ECC (capado a MAX_ECC_DRIFT). dxdy = desfase aplicado."""
    x0, y0, x1, y1 = coords
    pad = max(8, int(0.2 * min(x1-x0, y1-y0)))
    rx0, ry0 = max(0, x0-pad), max(0, y0-pad)
    rx1, ry1 = min(en_g.shape[1], x1+pad), min(en_g.shape[0], y1+pad)
    warp = ecc_refine(en_g[ry0:ry1, rx0:rx1], esw_g[ry0:ry1, rx0:rx1])
    es_full = es_w.copy().astype(np.float32); dxdy = None
    if warp is not None:
        d = float((warp[0, 2]**2 + warp[1, 2]**2) ** 0.5)
        if d <= MAX_ECC_DRIFT:
            dxdy = d
            reg = cv2.warpAffine(es_w[ry0:ry1, rx0:rx1], warp, (rx1-rx0, ry1-ry0),
                                 flags=cv2.INTER_CUBIC + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
            es_full[ry0:ry1, rx0:rx1] = reg.astype(np.float32)
    return es_full, dxdy


def precise_bubbles(en, es_w, de_b, ds_b, H, warp_ok=None):
    """Compone los globos sobre `en` con el pipeline preciso. Devuelve (res, stats, es_leftover).
    Los globos emparejados (Hungarian) y los de fallback usan EXACTAMENTE el mismo
    composite (ECC + contorno exacto); la diferencia es sólo el origen del globo.

    `warp_ok`: ¿la homografía de la página es de fiar? (medida directa del arte, ver
    homography_unreliable). None = sin medida -> se cae al criterio antiguo por tasa de globos."""
    res = [en.astype(np.float32).copy()]   # lista para mutar dentro del helper anidado
    en_g = cv2.cvtColor(en, cv2.COLOR_RGB2GRAY); esw_g = cv2.cvtColor(es_w, cv2.COLOR_RGB2GRAY)
    de_b = dedup(de_b); ds_b = dedup(ds_b)
    es_b_mapped = [map_box(H, b) for b in ds_b]
    gate = MATCH_GATE_FRAC * en.shape[0]
    pairs = match_hungarian(de_b, es_b_mapped, gate)
    matched_en = {i for i, _ in pairs}
    stats = dict(en=len(de_b), es=len(ds_b), match=len(pairs),
                 composed=0, rejected=0, fallback=0, ecc_max=0.0); shifts = []

    def try_compose(idx, require_ecc=False):
        """Compone el globo EN idx desde el español. require_ecc=True (fallback) sólo pega
        si el ECC logró una alineación AJUSTADA (drift<=cap): sin esa confirmación el texto
        podría quedar corrido (caja rectangular sin rasgos donde el IoU de forma no lo
        detecta). Devuelve True compuso / False rechazó / None sin máscara."""
        mm, coords = interior_mask(en, de_b[idx])
        if mm is None: return None
        x0, y0, x1, y1 = coords
        # ¿hay un globo ES con la misma forma en esa posición? (idioma-independiente)
        if shape_iou(interior_blob(en_g[y0:y1, x0:x1]), interior_blob(esw_g[y0:y1, x0:x1])) < IOU_MIN:
            return False
        es_full, dxdy = _aligned_es_region(en_g, esw_g, es_w, coords)
        if require_ecc and dxdy is None:
            return False   # el ECC no pudo confirmar alineación ajustada -> no arriesgar
        res[0] = _compose_exact(res[0], en.shape, mm, coords, es_full)
        if dxdy is not None: shifts.append(dxdy)
        return True

    for ei_b, _ in pairs:                      # emparejados: confían en Hungarian
        r = try_compose(ei_b)
        if r is True: stats['composed'] += 1
        elif r is False: stats['rejected'] += 1
    # FALLBACK POR GLOBO: globos EN sin match (el detector ES degradado los perdió) -> mismo
    # composite pero EXIGIENDO ECC ajustado (require_ecc) para no pegar texto corrido. GATE
    # DE CONFIANZA: sólo si la homografía de la página es confiable (>=50% globos compuestos);
    # en páginas con homografía rota (p024) el warp global da basura -> FALLBACK-ES de página.
    # ¿Es de fiar el warp? Con `warp_ok` se responde MIDIENDO EL ARTE. El criterio antiguo era la
    # TASA DE GLOBOS YA COMPUESTOS, y además de ser un proxy (mismo error que el gate de texto
    # libre, ver homography_unreliable) tenía una pescadilla que se muerde la cola: este fallback
    # existe PARA rescatar globos que el detector ES perdió, pero se condicionaba a que ya se
    # hubieran emparejado globos. Si los perdió TODOS, composed=0 -> el rescate se bloquea solo.
    # Medido: ch0001_045 (Sakamoto) = 1 globo EN, 0 globos ES -> conf 0.00 -> cobertura 0.000,
    # con art_mae 21.9 (página perfectamente alineada).
    if warp_ok if warp_ok is not None else (stats['composed'] / max(1, len(de_b)) >= FALLBACK_BUBBLE_MIN_CONF):
        for i in range(len(de_b)):
            if i in matched_en: continue
            x0, y0, x1, y1 = interior_mask(en, de_b[i])[1] or (0, 0, 0, 0)
            if x1 > x0 and not bg_consistent(en, es_w, x0, y0, x1, y1, max_diff=20): continue
            if try_compose(i, require_ecc=True) is True:
                stats['composed'] += 1; stats['fallback'] += 1
    stats['ecc_max'] = max(shifts) if shifts else 0.0
    # Burbujas ES que NINGUNA burbuja EN reclamó. Por ASIMETRÍA DE CLASE del detector
    # (una misma caption sin contorno la etiqueta como burbuja en un scan y como texto
    # libre en el otro), estas suelen corresponder a un texto_libre EN. Se devuelven para
    # que el paso de texto libre pueda emparejarlas (con sus guardas bg/art).
    matched_es = {j for _, j in pairs}
    es_leftover = [es_b_mapped[j] for j in range(len(ds_b)) if j not in matched_es]
    return res[0], stats, es_leftover


def _union_groups(nodes, gap):
    """Union-find por ADYACENCIA de borde sobre `nodes` [(box, is_es)]. Devuelve listas de
    índices (dentro de `nodes`), un grupo por componente conexa."""
    n = len(nodes)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    for i in range(n):
        for j in range(i+1, n):
            if edge_gap(nodes[i][0], nodes[j][0]) <= gap:
                ri, rj = find(i), find(j)
                if ri != rj: parent[ri] = rj
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _group_bbox(nodes, members):
    bxs = [nodes[m][0] for m in members]
    return [min(b[0] for b in bxs), min(b[1] for b in bxs),
            max(b[2] for b in bxs), max(b[3] for b in bxs)]


def _cluster_textfree(en_tf, es_tf_mapped, gap, max_area=None, page_area=None, min_gap=0.0):
    """Agrupa las cajas de texto libre EN y ES por ADYACENCIA de borde (union-find): dos
    cajas quedan en el mismo cluster si su `edge_gap` <= `gap`, sin importar el idioma. Esto
    une un caption EN con su(s) caja(s) ES re-tipografiadas en otra posición/desglose, y
    cose las piezas de un caption partido en varias cajas. Devuelve lista de clusters; cada
    cluster = (bbox_union, n_en, n_es).

    Un `gap` fijo SOBRE-FUSIONA cuando la página reparte muchos captions por el arte (páginas
    de monólogo): la cadena de adyacencias los une a todos en un bloque enorme que la guarda de
    área acaba descartando, dejando la página ENTERA en inglés aunque el ES estuviera bien
    emparejado. Por eso, si un cluster se pasa de `max_area`, se re-agrupa SOLO a sus miembros
    con un gap menor (recursivo): así se parte en captions individuales en vez de tirarse. Si
    ni al gap mínimo se puede partir (cajas que de verdad se tocan), se devuelve tal cual y la
    guarda de área decide — el fallo sigue siendo seguro (no se pega nada)."""
    nodes = [(b, 0) for b in en_tf] + [(b, 1) for b in es_tf_mapped]   # (box, is_es)
    cap = (max_area * page_area) if (max_area and page_area) else None

    def emit(members, g, depth):
        bbox = _group_bbox(nodes, members)
        too_big = cap is not None and area(bbox) > cap
        if too_big and len(members) > 1 and depth < TEXTFREE_SPLIT_MAXDEPTH:
            sub_gap = g * TEXTFREE_SPLIT_DECAY
            if sub_gap >= min_gap:
                subs = _union_groups([nodes[m] for m in members], sub_gap)
                if len(subs) > 1:                      # sólo si de verdad separa
                    out = []
                    for s in subs:
                        out += emit([members[i] for i in s], sub_gap, depth + 1)
                    return out
        n_en = sum(1 for m in members if nodes[m][1] == 0)
        return [(bbox, n_en, len(members) - n_en)]

    out = []
    for members in _union_groups(nodes, gap):
        out += emit(members, gap, 0)
    return out


def _erase_onlyen(res, en_g, esw_g, es_w, x0, y0, x1, y1, area, page_area, feather):
    """Pega la región ES sobre un cluster de texto SOLO-EN (sin ES emparejado), borrando así
    el inglés que quedaba sobre el arte. Devuelve (res, motivo_de_descarte|None).

    El clúster sale "solo-EN" por dos vías, y en AMBAS pegar el ES es lo correcto:
      a) el scan ES borró/reubicó ese texto (SFX) -> se pega su arte limpio;
      b) el ES SÍ lo tradujo pero el detector NO detectó su caja -> se pega el texto español.
    El único caso malo es que la región ES sea OTRO dibujo, y de eso responde el ANILLO.

    NO se veta por TINTA: dependía de la tipografía del scan (ratio ES/EN medido 0.17-7.47, sin
    valle) y tiraba traducciones correctas. Ver el bloque ONLYEN_INK_ADD_MAX (retirado) arriba."""
    if area > ONLYEN_MAX_FRAC * page_area:
        return res, "only-en:area", None
    # Dos candidatos: el warp de la homografía tal cual, y su refinado por ECC. NO se puede
    # exigir ECC (en un SFX pequeño no converge) ni preferirla a ciegas (aquí el EN tiene texto
    # y el ES no, así que ECC persigue los trazos del inglés y descuadra el arte). Se prueban
    # LOS DOS y gana el que mejor case el ANILLO de arte; si ninguno casa, no se toca nada.
    cands = [es_w.astype(np.float32)]
    es_full, dxdy = _aligned_es_region(en_g, esw_g, es_w, (x0, y0, x1, y1))
    if dxdy is not None:
        cands.append(es_full)
    # Motivo REAL del descarte por candidato (antes se devolvía siempre "only-en:ring": la rama
    # "ink" era CÓDIGO MUERTO porque `cands` nunca está vacío, y tres causas distintas salían
    # fusionadas bajo una etiqueta). Se guarda el MAE medido para poder ver la DISTRIBUCIÓN.
    best, best_mae = None, None
    whys, maes = [], []
    for c in cands:
        cg = cv2.cvtColor(np.clip(c, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
        mae = _ring_mae(en_g, cg, x0, y0, x1, y1)
        if mae is None:
            whys.append("nomeasure"); continue
        maes.append(mae)
        if mae > ONLYEN_RING_MAE:
            whys.append("ring"); continue
        if best_mae is None or mae < best_mae:
            best, best_mae = c, mae
    if best is None:
        tag = "ring" if "ring" in whys else "nomeasure"
        return res, f"only-en:{tag}", (min(maes) if maes else None)
    es_full = best
    h, w = en_g.shape[:2]
    m = cv2.GaussianBlur(np.full((y1-y0, x1-x0), 255, np.uint8), (0, 0), feather)
    a = np.zeros((h, w), np.float32); a[y0:y1, x0:x1] = m / 255.0; a = a[:, :, None]
    return es_full.astype(np.float32) * a + res * (1 - a), None, best_mae


def text_free(res, en, es_w, en_tf, es_tf_mapped, feather=4, es_rs=None):
    """Aplica el texto libre (captions) sobre `res` por CLUSTERING ESPACIAL. Devuelve matched.

    El emparejado 1:1 por distancia de CENTROIDE fallaba con los captions: el español se
    re-tipografía en otra posición/desglose (otra longitud de frase -> otro salto de línea ->
    otro bounding-box), así que su centroide se aleja del EN aunque el texto esté justo al
    lado, y los captions de varias líneas detectados como varias cajas se fragmentaban
    ('cortado'). En su lugar se AGRUPAN por adyacencia de borde las cajas EN y ES; cada
    cluster con texto en AMBOS idiomas se pega COMPLETO (es_w sobre el bbox del cluster: borra
    el texto EN y deja el ES, esté donde esté dentro del cluster). Mismas guardas bg/art.
    Los clusters GRANDES (>TEXTFREE_LARGE_FRAC) además exigen alineación ECC ajustada (si no,
    se saltan) para no dejar una costura de paste en páginas de homografía imprecisa."""
    h, w = en.shape[:2]; matched = 0
    skips = []                       # QA: (bbox, n_en, n_es, motivo) de cada cluster NO pegado
    if not en_tf:
        return res, 0, skips
    gap = TEXTFREE_GAP_FRAC * h
    page_area = float(w * h)
    en_g = cv2.cvtColor(en, cv2.COLOR_RGB2GRAY); esw_g = cv2.cvtColor(es_w, cv2.COLOR_RGB2GRAY)
    for bbox, n_en, n_es in _cluster_textfree(en_tf, es_tf_mapped, gap,
                                              max_area=TEXTFREE_CLUSTER_MAXAREA,
                                              page_area=page_area,
                                              min_gap=TEXTFREE_SPLIT_MINGAP * h):
        if n_en == 0:
            skips.append((bbox, n_en, n_es, "only-es", None))   # añadido del scan ES: no se pega
            continue
        x0, y0 = max(0, bbox[0]), max(0, bbox[1])
        x1, y1 = min(w, bbox[2]), min(h, bbox[3])
        if x1-x0 < 4 or y1-y0 < 4: continue
        area = (x1-x0) * (y1-y0)
        if n_es == 0:
            # SOLO-EN: SFX/caption sin ES emparejado. Ver bloque ONLYEN_* arriba.
            res, why, mae = _erase_onlyen(res, en_g, esw_g, es_w, x0, y0, x1, y1, area, page_area, feather)
            if why:
                skips.append((bbox, n_en, n_es, why, mae))
            else:
                matched += n_en
            continue
        if area > TEXTFREE_CLUSTER_MAXAREA * page_area:
            skips.append((bbox, n_en, n_es, "area", None))
            continue   # sobre-merge -> no arriesgar a pegar media página
        # Aquí iba la guarda de BRILLO (bg_consistent + indulto por anillo). RETIRADA: medía
        # DENTRO de la caja, donde el texto EN y ES difieren POR DEFINICIÓN, y tiraba traducción
        # correcta (22/22 de sus descartes tenían el ES ya detectado). Ver el bloque
        # "Guarda `bg` de TEXTO LIBRE — RETIRADA" arriba antes de pensar en reponerla.
        if not art_not_erased(en, es_w, x0, y0, x1, y1, max_extra=TEXTFREE_ART_MAXEXTRA):
            skips.append((bbox, n_en, n_es, "art", None)); continue
        # FUENTE DEL PEGADO: se elige la MEJOR ALINEADA por ANILLO de arte entre la homografía
        # (`es_w`) y la ES en IDENTIDAD (`es_rs`, sólo reescalada, sin perspectiva). Motivo: cuando
        # el scan ES y el EN NO comparten el encuadre de un panel, la homografía GLOBAL tuerce el
        # arte local; si el caption español (más largo) DESBORDA sobre una cara, el warp estampa esa
        # cara desalineada sobre el dibujo. La identidad casa el arte y evita el daño manteniendo el
        # español (MEDIDO ch0001_030: MAE de arte en el desborde warp 41 vs id 5; anillo warp 20 vs
        # id 4). El ANILLO (arte por FUERA de la caja, sin texto) discrimina las dos fuentes aunque
        # ambas pasen el umbral absoluto. En cluster GRANDE se añade el refinado ECC como candidato
        # y se EXIGE que el ganador tenga anillo <= umbral (sin ella un warp corrido dejaría una
        # costura grande); en cluster pequeño nunca se salta (se cae a `es_w` como antes).
        # OJO ECC: sólo en los GRANDES a propósito. Medido sobre 194 pastes de 45-49, aplicarlo a
        # TODOS empeora el encaje (anillo 14.1->16.4): ECC alinea la caja CON el texto dentro, y el
        # texto EN y ES difieren, así que persigue trazos de letras y tuerce el arte.
        large = area > TEXTFREE_LARGE_FRAC * page_area
        # DEFAULT = comportamiento previo, siempre pegable: en grande el refinado ECC si CONFIRMA,
        # si no la homografía; en pequeño la homografía. NO se le aplica gate de anillo (el original
        # confiaba en la confirmación ECC sola; ponerle gate tiraba pastes buenos -> regresión).
        es_full = dxdy = None
        if large:
            es_full, dxdy = _aligned_es_region(en_g, esw_g, es_w, (x0, y0, x1, y1))
        default = es_full if (dxdy is not None) else (None if large else es_w)
        # Se PREFIERE el candidato con menor ANILLO de arte (identidad gana cuando la homografía
        # tuerce el arte local y el caption español desborda sobre una cara; MEDIDO ch0001_030:
        # MAE desborde warp 41 vs id 5, anillo warp 20 vs id 4). El anillo (arte por FUERA de la
        # caja) discrimina las fuentes aunque ambas pasen el umbral absoluto.
        cands = [(es_w, esw_g)]
        if es_rs is not None:
            cands.append((es_rs, None))
        if es_full is not None:
            cands.append((es_full, None))
        best, best_rm = None, None
        for c, cg in cands:
            g = cg if cg is not None else cv2.cvtColor(np.clip(c, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
            rm = _ring_mae(en_g, g, x0, y0, x1, y1)
            if rm is None:
                continue
            if best_rm is None or rm < best_rm:
                best, best_rm = c, rm
        if default is None:
            # GRANDE sin ECC confirmado: no hay default seguro. Sólo se pega si el mejor candidato
            # casa el anillo (fallo seguro: sin ella un warp corrido dejaría costura grande).
            if best is None or best_rm > ONLYEN_RING_MAE:
                skips.append((bbox, n_en, n_es, "ecc", best_rm)); continue
            src = best
        else:
            # Hay default pegable (previo). Se usa el mejor por anillo si lo hay; si no, el default.
            src = best if best is not None else default
        m = cv2.GaussianBlur(np.full((y1-y0, x1-x0), 255, np.uint8), (0, 0), feather)
        a = np.zeros((h, w), np.float32); a[y0:y1, x0:x1] = m / 255.0; a = a[:, :, None]
        res = src.astype(np.float32) * a + res * (1 - a)
        matched += n_en
    return res, matched, skips


def align(es_paths, en_paths):
    eh = [dhash(cv2.cvtColor(load_rgb(p), cv2.COLOR_RGB2GRAY)) for p in es_paths]
    nh = [dhash(cv2.cvtColor(load_rgb(p), cv2.COLOR_RGB2GRAY)) for p in en_paths]
    n, m = len(eh), len(nh)
    D = np.array([[np.count_nonzero(eh[i] != nh[j]) for j in range(m)] for i in range(n)], float)
    C = np.full((n+1, m+1), 1e9); C[0, 0] = 0
    for i in range(1, n+1):
        for j in range(1, m+1): C[i, j] = D[i-1, j-1] + min(C[i-1, j-1], C[i-1, j], C[i, j-1])
    i, j, path = n, m, []
    while i > 0 and j > 0:
        path.append((i-1, j-1)); s = int(np.argmin([C[i-1, j-1], C[i-1, j], C[i, j-1]]))
        i, j = (i-1, j-1) if s == 0 else (i-1, j) if s == 1 else (i, j-1)
    return {nj: ei for ei, nj in path}


def es_fullpage(es, en):
    """Fallback: la página ES entera (ya en español, aunque sea de baja calidad),
    reescalada al tamaño de la página EN para mantener el tomo uniforme."""
    return cv2.resize(es, (en.shape[1], en.shape[0]), interpolation=cv2.INTER_CUBIC)


def _transplant_page(es, en):
    """Compone UNA página: arte EN + texto ES. Devuelve (res_rgb_uint8, note, is_fallback, dbg).

    `dbg` es un dict de diagnóstico SOLO para el modo QA (cobertura, globos compuestos/
    rechazados/fallback, ecc_max, y las cajas EN detectadas para el overlay). No afecta al
    resultado ni se usa fuera de testing — el caller lo ignora salvo si hay `debug_dir`."""
    H = homography(es, en)
    if H is None:
        # Sin alineación → probablemente el DTW emparejó páginas distintas o la página
        # no tiene suficientes features SIFT. Conservar arte EN: es de mayor calidad que
        # caer a la ES completa de baja calidad.
        dbg = {"coverage": None, "composed": 0, "rejected": 0, "fallback": 0, "ecc_max": 0.0,
               "en_bubbles": [], "en_free": [], "reason": "no-homography"}
        return en.copy(), "arte EN (sin homografía)", False, dbg
    es_w = cv2.warpPerspective(es, H, (en.shape[1], en.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    de, ds = detect(en), detect(es)
    # UNA sola medida de "¿está bien alineado este warp?" para los DOS gates de la página (el
    # fallback de globos y el de texto libre). Antes cada uno usaba su propio proxy por tasa de
    # globos compuestos, con los fallos descritos en homography_unreliable.
    tf_skipped, art_mae = homography_unreliable(en, es_w, de, ds, H)
    res, st, es_leftover = precise_bubbles(en, es_w, de[0], ds[0], H,
                                           warp_ok=(None if art_mae is None else not tf_skipped))
    # Candidatos de texto libre = text_free ES + burbujas ES no emparejadas
    # (cubre la asimetría de clase caption<->burbuja entre los dos scans).
    en_tf = dedup(de[2]); es_tf_mapped = [map_box(H, b) for b in dedup(ds[2])] + es_leftover
    # `tf_skipped` ya viene medido arriba (una sola medida para los dos gates de la página).
    if tf_skipped:
        matched, tf_skips = 0, []
    else:
        res, matched, tf_skips = text_free(res, en, es_w, en_tf, es_tf_mapped,
                                           es_rs=es_fullpage(es, en))
    res = np.clip(res, 0, 255).astype(np.uint8)
    # ¿se tradujo suficiente? texto = globos EN (dedup) + texto libre EN (dedup)
    en_bubbles = dedup(de[0])
    text_boxes = len(en_bubbles) + len(en_tf)
    translated = st['composed'] + matched
    cov = (translated / text_boxes) if text_boxes else 1.0
    dbg = {"coverage": round(cov, 4), "composed": st['composed'], "rejected": st['rejected'],
           "fallback": st['fallback'], "ecc_max": round(float(st.get('ecc_max', 0.0)), 3),
           "art_mae": (round(art_mae, 1) if art_mae is not None else None),
           "text_boxes": text_boxes, "translated": translated, "tf_skipped": tf_skipped,
           "en_bubbles": en_bubbles, "en_free": en_tf,
           "tf_skips": [{"bbox": [int(v) for v in b], "n_en": ne, "n_es": ns, "why": wy,
                         "mae": (round(float(mae), 1) if mae is not None else None)}
                        for b, ne, ns, wy, mae in tf_skips]}
    if text_boxes > 0 and cov < COVERAGE_MIN:
        # Cobertura baja: devolver el resultado parcial (lo que sí se pudo transplantar)
        # en vez de la página ES completa — el arte EN de alta calidad con texto parcial
        # es mejor que la ES de baja calidad con cero texto EN.
        dbg["reason"] = "low-coverage"
        return res, f"trasplante parcial ({cov:.0%})", False, dbg
    return res, f"trasplante (cobertura {cov:.0%})", False, dbg


def _qa_overlay(en_rgb, dbg):
    """EN con las cajas detectadas dibujadas: globos en azul, texto libre en cian. Solo QA."""
    img = cv2.cvtColor(en_rgb, cv2.COLOR_RGB2BGR).copy()
    for b in (dbg or {}).get("en_bubbles", []):
        cv2.rectangle(img, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (255, 120, 40), 3)
    for b in (dbg or {}).get("en_free", []):
        cv2.rectangle(img, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (220, 220, 40), 2)
    return img


def _es_fullpage_if_failed(res, note, is_fb, dbg, es, en):
    """Si `_transplant_page` no pudo trasplantar por falta de homografía (habría dejado el
    arte EN = texto en INGLÉS) y la página ES pasada es un match de contenido confiable,
    sustituye por la página ES completa (español). El objetivo del pipeline es producir
    español; una página ES de menor calidad es preferible a una página EN sin traducir."""
    if (dbg or {}).get("reason") == "no-homography":
        return es_fullpage(es, en), "ES completa (sin homografía)", True
    return res, note, is_fb


RESCUE_PAGE_MAE = 30   # MAE del ARTE de la página entera (EN vs ES warpeada) para aceptar que dos
                       # páginas de fuentes DISTINTAS son la MISMA. Medido en Amayo ch36 (37 págs
                       # ES x 6 EN): aciertos reales 2.0-8.5; falsos positivos 58-84. Valle VACÍO
                       # entre 9 y 58 (~6x) -> frontera real, no un dial. 30 cae en su centro.


def page_art_mae(es, en):
    """MAE del arte de la página completa tras alinear `es` sobre `en` por homografía.
    None si no hay homografía o el warp no cubre la página.

    Es el árbitro de "¿son la MISMA página?" entre fuentes distintas. Hace falta porque **la
    homografía SIFT SOLA NO BASTA**: entre páginas DISTINTAS del mismo manga (mismos bordes de
    viñeta, mismas tramas, mismo estilo) SIFT encuentra homografías espurias — medido: la pág 24
    del cap36 "alinea" con 2 páginas ES que son otras páginas. Aceptarlas pegaría DIÁLOGO DE OTRA
    PÁGINA, mucho peor que dejar el inglés. Comparar el dibujo entero sí las separa.
    Tampoco vale la `coverage`: engaña en páginas con texto japonés de fondo que el scan ES
    tampoco traduce (un acierto real puede dar 0.21). Esta medida no depende del texto."""
    H = homography(es, en)
    if H is None:
        return None
    w = cv2.warpPerspective(es, H, (en.shape[1], en.shape[0]))
    eg = cv2.cvtColor(en, cv2.COLOR_RGB2GRAY)
    wg = cv2.cvtColor(w, cv2.COLOR_RGB2GRAY)
    m = wg > 0                      # zona realmente cubierta por el warp
    if m.sum() < 0.3 * m.size:
        return None                 # apenas solapan: no es comparable
    return float(np.abs(eg[m].astype(np.float32) - wg[m].astype(np.float32)).mean())


def rescue_english_pages(english_pages, es_alt_dir, out_dir, should_cancel=None) -> list:
    """Segunda pasada para las páginas que se quedaron en INGLÉS: busca cada una en el MISMO
    capítulo de OTRA fuente ES y, si la encuentra, la trasplanta y reescribe en `out_dir`.
    Devuelve la lista de páginas que SIGUEN en inglés (para encadenar más fuentes).

    Que una fuente ES no traiga una página no significa que ninguna la traiga: cada scan tiene
    su propia paginación, extras y huecos (medido: Amayo ch36 p024 no existe en LeerCapitulo).

    Por qué NO puede empeorar la precisión: para aceptar una página se exige que el ARTE ENTERO
    case (`page_art_mae` <= RESCUE_PAGE_MAE). Si ninguna candidata lo cumple, no se toca nada y la
    página se queda en inglés, igual que ahora → sólo puede AÑADIR aciertos.
    OJO: NO basta con que SIFT encuentre homografía (da falsos positivos entre páginas distintas
    del mismo manga); ver `page_art_mae`. El dHash tampoco decide: sólo ORDENA por dónde empezar
    (es poco fiable — la misma página con texto EN vs ES difiere >80), así el acierto cae pronto.
    Coste: sólo corre sobre páginas YA fallidas (1-3 de ~35)."""
    es_paths = sorted(p for p in Path(es_alt_dir).glob("*.*")
                      if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if not es_paths:
        return list(english_pages)
    es_imgs = [(p, load_rgb(p)) for p in es_paths]
    still = []
    for item in english_pages:
        if should_cancel and should_cancel():
            still.append(item); continue
        en = load_rgb(item["en_src"])
        en_g = cv2.cvtColor(en, cv2.COLOR_RGB2GRAY)
        en_h = dhash(en_g)
        en_color = _is_color_page(en)
        order = sorted(es_imgs,
                       key=lambda t: np.count_nonzero(en_h != dhash(cv2.cvtColor(t[1], cv2.COLOR_RGB2GRAY))))
        hit = None
        for _p, es in order:
            if _is_color_page(es) and not en_color:
                continue                      # portada/créditos del scan: no tiene contrapartida
            mae = page_art_mae(es, en)
            if mae is None or mae > RESCUE_PAGE_MAE:
                continue                      # no es la misma página (o SIFT dio un falso positivo)
            r, nt, fb, db = _transplant_page(es, en)
            r, nt, fb = _es_fullpage_if_failed(r, nt, fb, db, es, en)
            if not str(nt).startswith("arte EN"):
                hit = (r, nt); break
        if hit is None:
            still.append(item); continue
        dest = Path(out_dir) / item["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(dest), cv2.cvtColor(hit[0], cv2.COLOR_RGB2BGR)):
            still.append(item); continue      # si no se pudo escribir, NO cantar victoria
    return still


def transplant_chapter(es_dir, en_dir, out_dir, file_prefix="ch0000",
                       progress_cb=None, should_cancel=None, debug_dir=None) -> dict:
    """Trasplanta un capítulo completo: empareja páginas ES↔EN, compone cada una y
    escribe `{file_prefix}_{i:03d}.png` en `out_dir`. Devuelve un resumen.

    progress_cb(done, total, note) — opcional, se llama tras cada página.
    should_cancel() -> bool — opcional; si devuelve True el trasplante para a mitad
    de capítulo y el resumen trae `cancelled: True` (el caller descarta el staging).
    debug_dir — SOLO modo QA (testing): si se pasa, vuelca por página el arte EN, la ES
    emparejada, un overlay con las cajas detectadas y `pages.json` con el diagnóstico. NO
    cambia el resultado; con `debug_dir=None` el comportamiento es idéntico al original."""
    es_dir, en_dir, out_dir = Path(es_dir), Path(en_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    es_paths = sorted(p for p in es_dir.glob("*.*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    en_paths = sorted(p for p in en_dir.glob("*.*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if not en_paths or not es_paths:
        return {"trans": 0, "fallback": 0, "pages": [], "error": "no input pages"}
    en2es = align(es_paths, en_paths)
    n_trans = n_fallback = n_english = 0
    english_pages = []   # páginas que se quedaron en INGLÉS: candidatas a rescate desde otra fuente ES
    pages = []
    total = len(en_paths)
    qa = None
    if debug_dir is not None:
        qa = Path(debug_dir); qa.mkdir(parents=True, exist_ok=True)
        qa_pages = {}
    for idx, en_p in enumerate(en_paths, 1):
        if should_cancel and should_cancel():
            return {"trans": n_trans, "fallback": n_fallback, "pages": pages, "cancelled": True}
        ei = idx - 1
        en = load_rgb(en_p)
        es = None
        es_used_idx = None   # QA: página ES REALMENTE usada (puede no ser la que emparejó el DTW)
        if ei in en2es:
            es_idx = en2es[ei]
            es_used_idx = es_idx
            es = load_rgb(es_paths[es_idx])
            en_g = cv2.cvtColor(en, cv2.COLOR_RGB2GRAY)
            es_g = cv2.cvtColor(es, cv2.COLOR_RGB2GRAY)
            hash_dist = int(np.count_nonzero(dhash(en_g) != dhash(es_g)))
            bad_match = (_is_color_page(es) and not _is_color_page(en)) or hash_dist > 80
            if bad_match:
                # El DTW emparejó una página ES incorrecta (portada de créditos, TOC…).
                # Buscar en páginas ES adyacentes la que mejor coincida con este arte EN
                # (dHash < 60). Caso típico: el scan ES tiene una portada extra al inicio
                # que el EN no tiene; la página real está en es_idx+1.
                best_es = None
                best_dist = 60  # sólo aceptar si el candidato es claramente mejor
                for cand in range(max(0, es_idx - 1), min(len(es_paths), es_idx + 4)):
                    if cand == es_idx:
                        continue
                    es_cand = load_rgb(es_paths[cand])
                    if _is_color_page(es_cand) and not _is_color_page(en):
                        continue
                    d = int(np.count_nonzero(dhash(en_g) != dhash(cv2.cvtColor(es_cand, cv2.COLOR_RGB2GRAY))))
                    if d < best_dist:
                        best_dist = d
                        best_es = es_cand
                        es_used_idx = cand
                if best_es is not None:
                    res, note, is_fb, dbg = _transplant_page(best_es, en)
                    res, note, is_fb = _es_fullpage_if_failed(res, note, is_fb, dbg, best_es, en)
                else:
                    # Sin vecina buena por dHash. El dHash da FALSOS POSITIVOS: la MISMA página con
                    # globos EN vs ES difiere >80 (páginas con mucho texto), y el umbral de vecino
                    # (<60) es más estricto que la puerta principal (≤80) → deja páginas reales en
                    # inglés. El árbitro FIABLE de "misma página" es la homografía SIFT (≥12 features).
                    # Se prueba la página emparejada + las vecinas B/N de la ventana; se acepta la
                    # PRIMERA que alinee (misma página) → español. El color se salta (portadas/anuncios
                    # de scan sin contrapartida en el arte). Si NINGUNA alinea → conservar arte EN.
                    cand_imgs = []
                    if not (_is_color_page(es) and not _is_color_page(en)):
                        cand_imgs.append((es_idx, es))
                    # Ventana asimétrica y AMPLIA hacia delante: cuando el DTW cayó en una página
                    # basura (anuncio de scan/portada de parte en la COSTURA de un capítulo troceado),
                    # la página real está varias posiciones MÁS ADELANTE (esa basura no existe en el
                    # arte y desplaza el alineado). Las candidatas van ordenadas por cercanía.
                    order = sorted(range(max(0, es_idx - 2), min(len(es_paths), es_idx + 9)),
                                   key=lambda c: abs(c - es_idx))
                    for cand in order:
                        if cand == es_idx:
                            continue
                        ec = load_rgb(es_paths[cand])
                        if _is_color_page(ec) and not _is_color_page(en):
                            continue
                        cand_imgs.append((cand, ec))
                    picked = None
                    for cidx, cimg in cand_imgs:
                        r, nt, fb, db = _transplant_page(cimg, en)
                        if (db or {}).get("reason") == "no-homography":
                            continue
                        # SIFT alineó -> ¿misma página? OJO: aquí el dHash YA es >80 (por eso
                        # estamos en esta rama), y una homografía se puede construir entre páginas
                        # DISTINTAS por features coincidentes (líneas/bordes de viñeta), así que "hay
                        # homografía" da FALSO POSITIVO. La señal de página equivocada es que el
                        # trasplante traduce CASI NADA: la misma página case la mayoría de globos, una
                        # alineación espuria da cobertura ínfima y estampa un globo de OTRA escena
                        # (MEDIDO Pink ch3 p30: es_027 (cena) "alineó" con en030 (baño), cov 0.14, y
                        # pegó "PERO... AMAS A EMA-CHAN" de otra página). Se exige cobertura >= umbral;
                        # si no, se sigue buscando y, si ninguna casa, la página queda en inglés
                        # (candidata a rescate desde otra fuente ES). No afecta a las parciales
                        # legítimas: esas tienen dHash <=80 y NO entran en esta rama.
                        cov = (db or {}).get("coverage")
                        if cov is not None and cov < COVERAGE_MIN:
                            continue
                        picked = (r, nt, fb, db, cidx)
                        break
                    if picked is not None:
                        res, note, is_fb, dbg, es_used_idx = picked
                    else:
                        es_used_idx = None
                        reason = "es-color-page" if (_is_color_page(es) and not _is_color_page(en)) else f"es-mismatch-d{hash_dist}"
                        res, note, is_fb, dbg = en.copy(), f"arte EN ({reason})", False, {"reason": reason}
            else:
                res, note, is_fb, dbg = _transplant_page(es, en)
                # Página ES CONFIABLE (mismo contenido) pero el trasplante por globos no pudo
                # alinear → antes se quedaba en INGLÉS (arte EN). Como el objetivo es ESPAÑOL,
                # caer a la página ES completa: peor resolución pero idioma correcto, no inglés.
                res, note, is_fb = _es_fullpage_if_failed(res, note, is_fb, dbg, es, en)
        else:
            # sin par ES -> deja el arte EN tal cual (no debería pasar con DTW completo)
            res, note, is_fb, dbg = en, "sin par ES (arte EN)", False, {"reason": "no-es-match"}
        if is_fb: n_fallback += 1
        else:     n_trans += 1
        fname = f"{file_prefix}_{idx:03d}.png"
        if str(note).startswith("arte EN") or str(note).startswith("sin par ES"):
            # Página que quedó en INGLÉS (esta fuente ES no la cubre). Se anota con su ruta de
            # arte para que el caller pueda intentar RESCATARLA desde otra fuente ES.
            n_english += 1
            english_pages.append({"file": fname, "en_src": str(en_p), "reason": (dbg or {}).get("reason")})
        cv2.imwrite(str(out_dir / fname), cv2.cvtColor(res, cv2.COLOR_RGB2BGR))
        pages.append(fname)
        if qa is not None:
            stem = fname[:-4]   # sin .png
            cv2.imwrite(str(qa / f"{stem}__en.png"), cv2.cvtColor(en, cv2.COLOR_RGB2BGR))
            # Vuelca la ES que se USÓ, no la que emparejó el DTW: cuando el corrector de
            # mismatch elige una vecina, volcar la original hace que el QA MIENTA (dice
            # "portada a color" en una página que se tradujo bien) y despista el diagnóstico.
            es_dump = load_rgb(es_paths[es_used_idx]) if es_used_idx is not None else es
            if es_dump is not None:
                cv2.imwrite(str(qa / f"{stem}__es.png"), cv2.cvtColor(es_dump, cv2.COLOR_RGB2BGR))
            cv2.imwrite(str(qa / f"{stem}__overlay.png"), _qa_overlay(en, dbg))
            qa_pages[fname] = {
                "note": note, "is_fallback": is_fb,
                "coverage": dbg.get("coverage"), "composed": dbg.get("composed"),
                "rejected": dbg.get("rejected"), "fallback": dbg.get("fallback"),
                "ecc_max": dbg.get("ecc_max"), "reason": dbg.get("reason"),
                "tf_skips": dbg.get("tf_skips"),
                "es_match_index": es_used_idx,
                "es_dtw_index": en2es.get(ei),   # lo que propuso el DTW (≠ si hubo corrección)
                "es_src": es_paths[es_used_idx].name if es_used_idx is not None else None,
                "en_src": en_p.name,
            }
            json.dump(qa_pages, open(qa / "pages.json", "w"), ensure_ascii=False, indent=2)
        if progress_cb:
            try: progress_cb(idx, total, note)
            except Exception: pass
    return {"trans": n_trans, "fallback": n_fallback, "english": n_english,
            "english_pages": english_pages,
            "en_pages": len(en_paths), "es_pages": len(es_paths), "pages": pages}
