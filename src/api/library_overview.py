"""Fusión «lo que hay en disco» + «lo que sigues» — la vista completa de la Biblioteca.

Esto vivía en el frontend (`views/LibraryView.vue::load()`): dos peticiones y ~60 líneas de
emparejamiento por id compuesto, por id y, en último término, por título en minúsculas. El sitio
correcto es el backend, donde ya viven `manga_identity.py` y `source_identity.py` — el navegador
no tiene por qué saber cómo se decide que una carpeta y una entrada seguida son la misma obra.

Módulo separado para mantener acotadas las responsabilidades de `library.py`.
"""
from api.observability import record_error


def _key(title: str) -> str:
    return (title or '').lower().strip()


def _identity(folder: str) -> dict:
    """La identidad canónica de `<carpeta>/.identity.json`, o `{}`.

    Devuelve el fichero entero y no sólo el `al_id` porque ahí dentro está también el `md_uuid`
    VERIFICADO (`manga_identity.resolve_identity`), que es la respuesta a «qué obra es ésta» para
    todo lo que cuelga de MangaDex. Medido en la biblioteca real: 23 de las 26 carpetas lo tienen y
    la vista no lo contaba, así que una obra descargada llegaba a la ficha sin identidad de MangaDex
    aunque estuviera resuelta y guardada al lado de sus páginas.
    Una lectura por carpeta y sólo de un fichero minúsculo, sobre el disco de la biblioteca; el
    coste está en el mismo orden que el `listdir` que ya hace el barrido. Ausente = esa obra aún
    no se ha resuelto (vacío legítimo, no se registra); ILEGIBLE sí se registra, que es la
    diferencia que este repo cobra cara.
    """
    if not folder:
        return {}
    from api.roots import series_dirs
    # En cualquiera de sus discos: la identidad se escribió junto a las páginas, y si la obra
    # vive sólo en el segundo disco, mirar el principal la daría por no identificada.
    p = next((d / '.identity.json' for d in series_dirs(folder)
              if (d / '.identity.json').exists()), None)
    if p is None:
        return {}
    try:
        import json
        data = json.loads(p.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        record_error('library_overview', e, op='identity', folder=folder)
        return {}


def build_overview(folders: list, tracked: list) -> list:
    """Cruza las carpetas del disco con las obras seguidas (`local_library.json`).

    `folders`  — salida de `GET /api/library` (carpetas medidas).
    `tracked`  — salida de `load_local_library()` (lo que has añadido a Mi Biblioteca).

    Devuelve las carpetas enriquecidas con su estado de seguimiento + las obras seguidas de las
    que aún no has descargado nada (`trackedOnly`), que si no serían invisibles.
    """
    by_id, by_name = {}, {}
    for t in (tracked or []):
        if not isinstance(t, dict):
            continue
        by_id[t.get('id')] = t
        k = _key(t.get('title'))
        if k:
            by_name[k] = t

    matched = set()
    seen = set()
    out = []

    for m in (folders or []):
        name_key = _key(m.get('name'))
        seen.add(name_key)
        sm = m.get('source_meta') or {}
        # Orden de preferencia: identidad compuesta de la fuente > id de carpeta > título.
        # El título es el ÚLTIMO recurso: es el que puede cruzar dos obras homónimas.
        t = None
        if sm.get('sourceId') and sm.get('mangaId'):
            t = by_id.get(f"src_{sm['sourceId']}_{sm['mangaId']}")
        if not t:
            t = by_id.get(m.get('id'))
        if not t:
            t = by_name.get(name_key)
        if t:
            matched.add(t.get('id'))
        kind = (t or {}).get('kind') or 'mangadex'
        ident = _identity(m.get('name'))
        out.append({
            **m,
            # El uuid de la entrada seguida manda; si no la hay, el VERIFICADO de la carpeta. Al
            # revés no: lo seguido es lo que el usuario eligió, la identidad es lo que dedujimos.
            'mdId': (t.get('id') if (t and kind == 'mangadex') else None) or ident.get('md_uuid'),
            'trackedId': (t or {}).get('id'),
            'status': (t or {}).get('status') or '',
            # El al_id (AniList) sale de la entrada seguida si la hay y, si no, de la identidad
            # canónica que ya vive en la carpeta (`.identity.json`). Sin esto, una obra DESCARGADA
            # llegaba a la ficha sin al_id — y todo lo que cuelga de él (puente manga⇄anime,
            # frescura de versiones) quedaba mudo justo en las obras que más usas.
            'al_id': (t or {}).get('al_id') or ident.get('al_id'),
        })

    for t in (tracked or []):
        if not isinstance(t, dict):
            continue
        k = _key(t.get('title'))
        if not k or k in seen or t.get('id') in matched:
            continue
        seen.add(k)
        kind = t.get('kind') or 'mangadex'
        source_meta = None
        if kind == 'source' and t.get('source_id') and t.get('manga_id'):
            source_meta = {
                'sourceId': t.get('source_id'),
                'mangaId': t.get('manga_id'),
                'sourceName': t.get('source_name') or '',
                'sourceLang': t.get('source_lang') or '',
            }
        out.append({
            'id': t.get('title'), 'name': t.get('title'),
            'chapter_count': 0, 'image_count': 0, 'page_count': 0, 'upscaled': 0,
            'cover': t.get('cover'),
            'mdId': t.get('id') if kind == 'mangadex' else None,
            'trackedId': t.get('id'), 'trackedOnly': True,
            'status': t.get('status') or '',
            'al_id': t.get('al_id'),
            'kind': t.get('kind'), 'novel': t.get('novel'),
            'source_meta': source_meta,
        })

    return out


def safe_tracked(loader) -> list:
    """`local_library.json` roto no puede vaciar la biblioteca: se registra y se sigue.

    Ausente = vacío legítimo (no se loguea). Ilegible = fallo (sí se loguea) — el resto de la
    vista, que son las carpetas del disco, se sirve igual.
    """
    try:
        return loader() or []
    except Exception as e:
        record_error('library', e, op='load_local_library')
        return []
