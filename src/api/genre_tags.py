#!/usr/bin/env python3
"""Géneros ESPECÍFICOS a partir de las etiquetas de AniList.

Los `genres` de AniList son 18 y muy gruesos: «Romance» no distingue un GL de un shōnen de
instituto, y buscar «algo de yuri» en tu biblioteca era imposible. Las `tags` sí lo saben
(«Yuri» sale con rango 96-99 en las obras GL), pero son **cientos** y muchas son descriptores
que no sirven para elegir qué leer («Female Protagonist», «Ensemble Cast», «Tsundere»).

Así que se filtran dos veces: una lista CURADA de etiquetas que responden a «¿de qué tipo de
historia es esto?», y un rango mínimo — la etiqueta la votan los usuarios de AniList, y por
debajo de ~60 es una opinión suelta, no una característica de la obra.

Vive en su propio módulo porque lo usan dos sitios (`anilist.py` para manga, `anime.py` para
anime) y la lista tiene que ser LA MISMA: si «Yuri» se llama distinto en cada uno, el filtro de
manga y el de anime dejan de ser el mismo filtro.
"""

# Nombre EXACTO de la etiqueta en AniList. Se guarda tal cual (es la clave con la que habla la
# API); la traducción al español vive en el front (`lib/etiquetas.js`), como los géneros.
CURATED_TAGS = frozenset({
    # Lo que el usuario pidió por su nombre: géneros que los `genres` de AniList no distinguen.
    'Yuri', "Boys' Love", 'Bara', 'LGBTQ+ Themes',
    # Demografía: dice el tono mejor que cualquier género.
    'Josei', 'Seinen', 'Shounen', 'Shoujo',
    # Tipo de historia.
    'Isekai', 'School', 'Historical', 'Military', 'Magic', 'Vampire', 'Time Manipulation',
    'Post-Apocalyptic', 'Survival', 'Revenge', 'Tragedy', 'Iyashikei', 'Cooking', 'Idol',
    'Gyaru', 'Detective', 'Super Power', 'Space',
})

TAG_MIN_RANK = 60      # por debajo, la etiqueta es una opinión suelta y ensucia el desplegable
TAG_MAX = 4            # por obra: con más, una sola obra llena el filtro de opciones de una


def pick_tags(tags) -> list:
    """Etiquetas curadas de una obra, las mejor votadas primero.

    `tags` es la lista de AniList `[{name, rank}]`. Devuelve nombres, nunca más de `TAG_MAX`.
    """
    buenas = [t for t in (tags or [])
              if t.get('name') in CURATED_TAGS and (t.get('rank') or 0) >= TAG_MIN_RANK]
    buenas.sort(key=lambda t: -(t.get('rank') or 0))
    return [t['name'] for t in buenas[:TAG_MAX]]
