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
TOL_FRAC = 0.06          # tolerancia de búsqueda de texto libre = 6% del alto
FALLBACK_BUBBLE_MIN_CONF = 0.50  # sólo recuperar globos sueltos si la homografía de la
                                 # página es confiable (>=50% de globos ya emparejados)
COVERAGE_MIN = 0.50       # si se tradujo <50% del texto de la página -> usar la página ES completa
TEXTFREE_BG_MAXDIFF = 30  # umbral de brillo para texto libre (captions sobre arte); ver text_free()
TEXTFREE_ART_MAXEXTRA = 30  # umbral de std para texto libre: un scan ES más BLANDO (menos
                            # contraste) que el EN da std_diff +12..27 con el MISMO arte
                            # (medianDiff~0). Con 11 se rechazaban captions reales; los pegados
                            # malos (blanco sobre arte) los caza bg_consistent (medianDiff 30-233).


def load_rgb(p): return cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
def dhash(g, hs=16): g = cv2.resize(g, (hs+1, hs)); return (g[:, 1:] > g[:, :-1]).flatten()
def cxcy(b): return ((b[0]+b[2])/2.0, (b[1]+b[3])/2.0)
def area(b): return max(1, (b[2]-b[0])*(b[3]-b[1]))


def box_iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1-ix0) * max(0, iy1-iy0)
    return inter / max(1, area(a) + area(b) - inter)


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
    orb = cv2.ORB_create(4000)
    k1, d1 = orb.detectAndCompute(eg, None); k2, d2 = orb.detectAndCompute(ng, None)
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
    M, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0)
    if M is None: return None
    return np.vstack([M, [0.0, 0.0, 1.0]])   # 2x3 afín -> 3x3 para warpPerspective/map_box


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
    if comp is None or frac < 0.05: return None, None
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
    (interior blanco) y OSCUROS (interior negro)."""
    if gray_crop.size == 0: return None
    comp, _ = _dominant_component(gray_crop)
    if comp is None: return None
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


def bg_consistent(en, es_w, x0, y0, x1, y1, max_diff=20):
    ae = cv2.cvtColor(en[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    be = cv2.cvtColor(es_w[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    if ae.size == 0: return False
    return abs(float(np.median(ae)) - float(np.median(be))) < max_diff


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


def precise_bubbles(en, es_w, de_b, ds_b, H):
    """Compone los globos sobre `en` con el pipeline preciso. Devuelve (res, stats, es_leftover).
    Los globos emparejados (Hungarian) y los de fallback usan EXACTAMENTE el mismo
    composite (ECC + contorno exacto); la diferencia es sólo el origen del globo."""
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
    if stats['composed'] / max(1, len(de_b)) >= FALLBACK_BUBBLE_MIN_CONF:
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


def text_free(res, en, es_w, en_tf, es_tf_mapped, feather=4):
    """Aplica el texto libre emparejado sobre `res` (in place via blend). Devuelve matched."""
    h, w = en.shape[:2]; tol = TOL_FRAC * h; matched = 0
    full = np.zeros((h, w), np.float32)
    # Emparejado GLOBAL (Hungarian) EN<->ES por DISTANCIA DE CENTRO pura: el greedy "el más
    # cercano libre" provocaba ROBOS (una caja EN agarraba el ES que le tocaba a otra y la
    # dejaba sin traducir). El óptimo global lo evita. Se gatea por tolerancia tras asignar.
    pairs = []
    if en_tf and es_tf_mapped:
        C = np.zeros((len(en_tf), len(es_tf_mapped)))
        for i, eb in enumerate(en_tf):
            ec = cxcy(eb)
            for j, sb in enumerate(es_tf_mapped):
                sc = cxcy(sb)
                C[i, j] = ((ec[0]-sc[0])**2 + (ec[1]-sc[1])**2) ** 0.5
        ri, cj = linear_sum_assignment(C)
        pairs = [(i, j) for i, j in zip(ri, cj) if C[i, j] <= tol]
    for i_en, j_es in pairs:
        box = en_tf[i_en]; mb = es_tf_mapped[j_es]
        union = [min(box[0], mb[0]), min(box[1], mb[1]), max(box[2], mb[2]), max(box[3], mb[3])]
        x0, y0, x1, y1 = [max(0, union[0]), max(0, union[1]), min(w, union[2]), min(h, union[3])]
        if x1-x0 < 4 or y1-y0 < 4: continue
        # bg_consistent con umbral MÁS LAXO para TEXTO LIBRE (captions sobre arte): la caja
        # union abarca mucho fondo y dos escaneos distintos del MISMO arte difieren en brillo
        # ~20-30 (no es un globo blanco sobre arte, que daría 45-150). El umbral de 20 daba
        # falsos rechazos (caption en inglés sin traducir). La guarda de ESTRUCTURA
        # (art_not_erased) sigue protegiendo de tapar arte real. El fallback de globos sí
        # usa 20 (ahí un pegado blanco-sobre-arte sí debe rechazarse).
        if not bg_consistent(en, es_w, x0, y0, x1, y1, max_diff=TEXTFREE_BG_MAXDIFF): continue
        if not art_not_erased(en, es_w, x0, y0, x1, y1, max_extra=TEXTFREE_ART_MAXEXTRA): continue
        m = cv2.GaussianBlur(np.full((y1-y0, x1-x0), 255, np.uint8), (0, 0), feather)
        full[y0:y1, x0:x1] = np.maximum(full[y0:y1, x0:x1], m/255.0)
        matched += 1
    a = full[:, :, None]
    res = es_w.astype(np.float32) * a + res * (1 - a)
    return res, matched


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
    """Compone UNA página: arte EN + texto ES. Devuelve (res_rgb_uint8, note, is_fallback)."""
    H = homography(es, en)
    if H is None:
        # sin alineación posible -> página ES completa (en español, baja calidad)
        return es_fullpage(es, en), "FALLBACK-ES (sin homografía)", True
    es_w = cv2.warpPerspective(es, H, (en.shape[1], en.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    de, ds = detect(en), detect(es)
    res, st, es_leftover = precise_bubbles(en, es_w, de[0], ds[0], H)
    # Candidatos de texto libre = text_free ES + burbujas ES no emparejadas
    # (cubre la asimetría de clase caption<->burbuja entre los dos scans).
    en_tf = dedup(de[2]); es_tf_mapped = [map_box(H, b) for b in dedup(ds[2])] + es_leftover
    res, matched = text_free(res, en, es_w, en_tf, es_tf_mapped)
    res = np.clip(res, 0, 255).astype(np.uint8)
    # ¿se tradujo suficiente? texto = globos EN (dedup) + texto libre EN (dedup)
    text_boxes = len(dedup(de[0])) + len(en_tf)
    translated = st['composed'] + matched
    cov = (translated / text_boxes) if text_boxes else 1.0
    if text_boxes > 0 and cov < COVERAGE_MIN:
        # la página no se pudo trasplantar (otra maquetación) -> ES completa
        return es_fullpage(es, en), f"FALLBACK-ES (cobertura {cov:.0%})", True
    return res, f"trasplante (cobertura {cov:.0%})", False


def transplant_chapter(es_dir, en_dir, out_dir, file_prefix="ch0000",
                       progress_cb=None, should_cancel=None) -> dict:
    """Trasplanta un capítulo completo: empareja páginas ES↔EN, compone cada una y
    escribe `{file_prefix}_{i:03d}.png` en `out_dir`. Devuelve un resumen.

    progress_cb(done, total, note) — opcional, se llama tras cada página.
    should_cancel() -> bool — opcional; si devuelve True el trasplante para a mitad
    de capítulo y el resumen trae `cancelled: True` (el caller descarta el staging)."""
    es_dir, en_dir, out_dir = Path(es_dir), Path(en_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    es_paths = sorted(p for p in es_dir.glob("*.*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    en_paths = sorted(p for p in en_dir.glob("*.*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if not en_paths or not es_paths:
        return {"trans": 0, "fallback": 0, "pages": [], "error": "no input pages"}
    en2es = align(es_paths, en_paths)
    n_trans = n_fallback = 0
    pages = []
    total = len(en_paths)
    for idx, en_p in enumerate(en_paths, 1):
        if should_cancel and should_cancel():
            return {"trans": n_trans, "fallback": n_fallback, "pages": pages, "cancelled": True}
        ei = idx - 1
        en = load_rgb(en_p)
        if ei in en2es:
            es = load_rgb(es_paths[en2es[ei]])
            res, note, is_fb = _transplant_page(es, en)
        else:
            # sin par ES -> deja el arte EN tal cual (no debería pasar con DTW completo)
            res, note, is_fb = en, "sin par ES (arte EN)", False
        if is_fb: n_fallback += 1
        else:     n_trans += 1
        fname = f"{file_prefix}_{idx:03d}.png"
        cv2.imwrite(str(out_dir / fname), cv2.cvtColor(res, cv2.COLOR_RGB2BGR))
        pages.append(fname)
        if progress_cb:
            try: progress_cb(idx, total, note)
            except Exception: pass
    return {"trans": n_trans, "fallback": n_fallback, "pages": pages}
