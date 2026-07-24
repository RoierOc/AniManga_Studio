"""Arte HORIZONTAL para el hero de la Biblioteca de manga.

La cabecera de manga usaba la portada (2:3) difuminada para llenar un marco panorámico, porque
no teníamos nada ancho. AniList sí publica un `bannerImage` para las entradas de tipo MANGA —
horizontal, y de la obra, no de una adaptación.

MEDIDO sobre la biblioteca real (28 obras, 2026-07-23):
  · `bannerImage` de MANGA por búsqueda directa ......... 19/28 = 67 %
  · rescate vía adaptación a ANIME (la ruta de TMDB) .....  0/9  de las que faltaban
  · rescate limpiando el título (paréntesis, subtítulo) ...  0/9

Por eso la fuente es AniList y NO TMDB: TMDB no indexa manga, sólo llegaría por la adaptación a
anime, y las 9 obras sin banner son de nicho y no tienen adaptación. Ese 33 % restante cae al
comportamiento que ya existía (portada difuminada en `MediaHero`), que es un fallback digno.

⚠️ GOTCHA MEDIDO — **no reintentes agrupar la consulta con alias de GraphQL**. Parecía la opción
lista (una petición para los 5 títulos) y funciona… hasta que UNO no existe: AniList responde
entonces **HTTP 404 y pone a `null` TODOS los alias**, no sólo el que falló. Con
`["Ao no Hako", "Amayo no Tsuki", "Honto wa Motto Shitai dake", "Asagiiro no Saudade"]` se
perdieron los dos que sí tenían banner por culpa del cuarto. Es todo-o-nada, así que va una
petición por título.

Lo que hace barato ese bucle es la **caché en disco de 7 días, que guarda también los fallos**:
si no, las obras que nunca van a resolver se reconsultarían en cada carga del hero y se comerían
el rate limit de AniList (~30 req/min degradado). En régimen estable son CERO peticiones.
"""
from api.resilient_http import http as _http
from api.runtime import cache_get, cache_set
from api.observability import record_error

_URL = 'https://graphql.anilist.co'
_NS = 'manga_banner'
_TTL = 7 * 24 * 3600
_MAX = 24                  # tope de títulos por lote: el hero pide 5, esto es margen de sobra

# `MISS` se cachea igual que un acierto: distingue "lo buscamos y no hay" de "no lo hemos
# buscado". Sin esto, las obras sin banner se reconsultan en CADA carga (regla: falló ≠ no había).
_MISS = ''


_Q = 'query($s: String) { Media(search: $s, type: MANGA) { bannerImage } }'


def _fetch_one(title: str):
    """URL del banner, `_MISS` si la obra no está o no tiene, `None` si la CONSULTA falló.

    Los tres casos son distintos a propósito: sólo los dos primeros se pueden cachear. Cachear
    un fallo de red como "no hay banner" lo congelaría 7 días (regla: falló ≠ no había).
    """
    try:
        r = _http.post(_URL, json={'query': _Q, 'variables': {'s': title}}, timeout=15)
        body = r.json()
    except Exception as e:
        record_error('anilist', e, op='manga_banner', title=title)
        return None

    # 404 = "esa obra no existe en AniList". Es una RESPUESTA, no una avería: se cachea como
    # fallo para no volver a preguntar por ella en 7 días.
    if r.status_code == 404:
        return _MISS
    if r.status_code >= 400:
        record_error('anilist', RuntimeError(f'HTTP {r.status_code}'), op='manga_banner', title=title)
        return None

    media = ((body or {}).get('data') or {}).get('Media') or {}
    return media.get('bannerImage') or _MISS


def banners_for(titles: list) -> dict:
    """`{título: url_banner}` — sólo los que TIENEN banner. Nunca lanza."""
    want = [t for t in dict.fromkeys(titles or []) if t][:_MAX]
    out = {}
    for t in want:
        hit = cache_get(_NS, t, _TTL)
        if hit is None:
            hit = _fetch_one(t)
            if hit is None:          # la consulta falló: ni se cachea ni se da por vacío
                continue
            cache_set(_NS, t, hit, ttl=_TTL, max_entries=600)
        if hit != _MISS:
            out[t] = hit
    return out
