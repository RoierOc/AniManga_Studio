"""Clasificación robusta del idioma de una pista de subtítulos — español y sus variantes.

Por qué existe: mpv (`--slang`) y la lógica antigua sólo miraban el CÓDIGO de idioma de la
pista (`spa`/`es`). Pero los grupos de fansub marcan el español de mil formas, y muy a menudo
la única señal está en el TÍTULO de la pista, no en el código: "LAT", "SPA-LAT", "Spanish
(LAT)", "Español Latino", "Castellano", "es-419"… Un contenedor puede traer `language=und` y
`title="Spanish [LAT]"` y aun así ser exactamente lo que el usuario quiere.

Este módulo es el ÚNICO lugar que decide qué es español (y de qué variante), mirando código Y
título con un criterio amplio pero seguro (límites de palabra para no casar dentro de otras,
p.ej. 'lat' dentro de 'translate'). Lo comparten la auto-selección del reproductor y la lógica
de "ya está en español" (no re-traducir), así no pueden desincronizarse.

Variantes que distingue (todas son español para `is_es`):
  · 'lat' — español latinoamericano (lo que el usuario prioriza)
  · 'cas' — castellano / España
  · 'es'  — español genérico / sin región

Diseñado para ser general y mantenible: para cubrir una etiqueta nueva basta añadir un patrón
aquí, y tanto la selección como la no-retraducción lo heredan.
"""
import re

# ── Códigos de idioma (BCP-47 / ISO, ya normalizados a minúsculas con '-') ────────────────
_LAT_CODES = {
    'es-419', 'es-la', 'es-lat', 'es-mx', 'es-ar', 'es-co', 'es-cl', 'es-pe', 'es-ve',
    'es-bo', 'es-ec', 'es-gt', 'es-hn', 'es-ni', 'es-pa', 'es-py', 'es-sv', 'es-uy', 'es-do',
    'spa-419', 'spa-mx', 'spa-la', 'spa-lat', 'lat', 'lat-am', 'latam',
}
_CAS_CODES = {'es-es', 'spa-es', 'cas', 'es-cas'}
# Español genérico (código completo o su parte base antes del guión).
_ES_CODES = {'es', 'spa', 'esp', 'esl', 'spanish', 'español', 'espanol', 'castellano'}

# ── Palabras dentro del TÍTULO (o de un código con texto libre) ───────────────────────────
# \b evita casar dentro de otras palabras ('translate', 'related', 'gelatin'…).
_LAT_WORDS = re.compile(
    r'\b(lat|latino?s?|latin[oa]?\w*|latam|latinoameric\w*|hispanoameric\w*|sudameric\w*|'
    r'm[eé]xic\w*|mexican[oa]?|es-?419)\b', re.I)
_CAS_WORDS = re.compile(r'\b(cast\w*|castilian|espa[nñ]a|iberic\w*|es-?es)\b', re.I)
_ES_WORDS = re.compile(r'\b(spa|spanish|espa[nñ]ol|espanol|esp|castellano)\b', re.I)


def _norm(s: str) -> str:
    return (s or '').strip().lower().replace('_', '-')


def classify_es(lang: str = '', title: str = '') -> str | None:
    """Clasifica una pista: 'lat' | 'cas' | 'es' | None, mirando código Y título.

    El orden importa: primero latino (lo más específico y prioritario), luego castellano,
    luego español genérico. Cualquier resultado != None significa "es español"."""
    lc = _norm(lang)
    tt = title or ''
    # Latino — el más específico; gana si aparece en código o título.
    if lc in _LAT_CODES or _LAT_WORDS.search(lc) or _LAT_WORDS.search(tt):
        return 'lat'
    # Castellano / España.
    if lc in _CAS_CODES or _CAS_WORDS.search(lc) or _CAS_WORDS.search(tt):
        return 'cas'
    # Español genérico: código exacto, parte base del código, o palabra en el título.
    if lc in _ES_CODES or lc.split('-')[0] in _ES_CODES or _ES_WORDS.search(tt):
        return 'es'
    return None


def is_es(lang: str = '', title: str = '') -> bool:
    """¿La pista está en español (cualquier variante)?"""
    return classify_es(lang, title) is not None


# Menor = mejor. Latino primero (preferencia del usuario), luego genérico, luego castellano.
_RANK = {'lat': 0, 'es': 1, 'cas': 2}


def es_rank(lang: str = '', title: str = '') -> int:
    """Prioridad de una pista como español (0 = mejor); 99 si no es español."""
    return _RANK.get(classify_es(lang, title), 99)


def best_es_index(tracks: list) -> int:
    """Índice 0-based de la MEJOR pista española de una lista de dicts {lang/language, title},
    o -1 si ninguna lo es. Estable: ante empate de variante, gana la primera del contenedor."""
    best_i, best_r = -1, 99
    for i, t in enumerate(tracks):
        lang = t.get('lang') or t.get('language') or ''
        title = t.get('title') or t.get('track_name') or ''
        r = es_rank(lang, title)
        if r < best_r:
            best_i, best_r = i, r
    return best_i
