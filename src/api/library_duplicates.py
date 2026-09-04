"""Auditor de duplicados de la biblioteca de manga.

La carpeta y la entrada de seguimiento no son dos obras duplicadas: son dos vistas de la
misma obra. Este módulo compara cada ámbito por separado y sólo marca como duplicado lo que
puede repetirse dentro de ese ámbito (dos carpetas o dos seguimientos).

La identidad fuerte sale únicamente de `.identity.json` (MangaDex verificado), del AniList-id
o de un UUID MangaDex explícito en el seguimiento. `.source_meta.json` no se usa: describe de
dónde se descarga una versión, no qué obra es. El título normalizado queda como coincidencia
de revisión, nunca como una fusión automática.
"""
from __future__ import annotations

from collections import defaultdict
import json
import re
import unicodedata
from pathlib import Path

from api.observability import record_error


_MD_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def canonical_title(title: str) -> str:
    """Normaliza un título para detectar cambios de puntuación, espacios o acentos."""
    value = unicodedata.normalize("NFKD", str(title or "")).casefold()
    return "".join(char for char in value if char.isalnum())


def _md_uuid(value) -> str:
    value = str(value or "").strip()
    return value if _MD_UUID.fullmatch(value) else ""


def _positive_int(value):
    try:
        number = int(str(value).strip())
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _folder_identity(title: str) -> dict:
    """Lee la identidad canónica local sin resolver nada en red."""
    if not title:
        return {}
    try:
        from api.roots import series_dirs

        for directory in series_dirs(title):
            path = directory / ".identity.json"
            if not path.is_file():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                return value if isinstance(value, dict) else {}
            except Exception as exc:
                record_error("library_duplicates", exc, op="read_identity", title=title)
                return {}
    except Exception as exc:
        record_error("library_duplicates", exc, op="find_identity", title=title)
    return {}


def _folder_record(folder: dict, identity_loader) -> dict | None:
    if not isinstance(folder, dict):
        return None
    name = str(folder.get("name") or folder.get("id") or "").strip()
    title_key = canonical_title(name)
    if not name or not title_key:
        return None

    identity = identity_loader(name) or {}
    md_id = _md_uuid(identity.get("md_uuid") or folder.get("md_id") or folder.get("mdId"))
    al_id = _positive_int(identity.get("al_id") or folder.get("al_id"))
    strong = ([f"md:{md_id}"] if md_id else []) + ([f"al:{al_id}"] if al_id else [])
    return {
        "id": f"folder:{name}",
        "kind": "folder",
        "name": name,
        "cover": folder.get("cover"),
        "chapter_count": int(folder.get("chapter_count") or 0),
        "page_count": int(folder.get("page_count") or folder.get("image_count") or 0),
        "upscaled": int(folder.get("upscaled") or 0),
        "md_id": md_id or None,
        "al_id": al_id,
        "identity_method": identity.get("method") or "",
        "source_meta": folder.get("source_meta"),
        "title_key": title_key,
        "_strong": strong,
    }


def _source_meta(entry: dict) -> dict | None:
    if entry.get("kind") != "source" or not entry.get("source_id") or not entry.get("manga_id"):
        return None
    return {
        "sourceId": entry.get("source_id"),
        "mangaId": entry.get("manga_id"),
        "sourceName": entry.get("source_name") or "",
        "sourceLang": entry.get("source_lang") or "",
    }


def _tracking_record(entry: dict) -> dict | None:
    if not isinstance(entry, dict) or entry.get("novel") or entry.get("kind") == "novel":
        return None
    name = str(entry.get("title") or entry.get("name") or "").strip()
    title_key = canonical_title(name)
    if not name or not title_key:
        return None

    tracked_id = str(entry.get("id") or entry.get("mangaId") or "").strip()
    md_id = _md_uuid(
        entry.get("md_id")
        or entry.get("mdId")
        or entry.get("mangaDexId")
        or (tracked_id if _MD_UUID.fullmatch(tracked_id) else "")
    )
    al_id = _positive_int(entry.get("al_id") or entry.get("anilist") or entry.get("anilist_id"))
    strong = ([f"md:{md_id}"] if md_id else []) + ([f"al:{al_id}"] if al_id else [])
    return {
        "id": f"tracking:{tracked_id or name}",
        "kind": "tracking",
        "name": name,
        "cover": entry.get("cover"),
        "tracked_id": tracked_id or None,
        "status": entry.get("status") or "",
        "source_name": entry.get("source_name") or "",
        "source_lang": entry.get("source_lang") or "",
        "source_meta": _source_meta(entry),
        "md_id": md_id or None,
        "al_id": al_id,
        "title_key": title_key,
        "_strong": strong,
    }


class _DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _groups(records: list[dict], scope: str) -> list[dict]:
    if len(records) < 2:
        return []

    dsu = _DisjointSet(len(records))
    strong_owner: dict[str, int] = {}
    duplicate_strong: set[str] = set()
    for index, record in enumerate(records):
        for key in record["_strong"]:
            previous = strong_owner.get(key)
            if previous is None:
                strong_owner[key] = index
            else:
                duplicate_strong.add(key)
                dsu.union(previous, index)

    # El título sólo une registros que no tienen identidad fuerte. Si una misma etiqueta tiene
    # dos UUID distintos, no los mezclamos: eso es precisamente lo que esta pantalla debe hacer
    # visible para revisión manual.
    by_title: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        by_title[record["title_key"]].append(index)
    review_titles: set[str] = set()
    for title_key, indexes in by_title.items():
        if len(indexes) < 2 or any(records[index]["_strong"] for index in indexes):
            continue
        review_titles.add(title_key)
        for index in indexes[1:]:
            dsu.union(indexes[0], index)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(records)):
        components[dsu.find(index)].append(index)

    out = []
    for indexes in components.values():
        if len(indexes) < 2:
            continue
        keys = [key for key in duplicate_strong
                if any(key in records[index]["_strong"] for index in indexes)]
        exact = bool(keys)
        if exact:
            if any(key.startswith("md:") for key in keys):
                reason = "Identidad MangaDex compartida"
            else:
                reason = "Identidad AniList compartida"
        else:
            # Sólo puede llegar aquí una unión por título sin identidad fuerte.
            reason = "Título normalizado; revisar antes de unificar"
        public_records = []
        for index in sorted(indexes, key=lambda item: records[item]["name"].casefold()):
            public = {key: value for key, value in records[index].items() if not key.startswith("_")}
            public_records.append(public)
        out.append({
            "id": f"{scope}:{public_records[0]['id']}",
            "scope": scope,
            "confidence": "exact" if exact else "review",
            "reason": reason,
            "records": public_records,
        })
    return out


def scan_duplicates(folders: list, tracked: list, identity_loader=None) -> dict:
    """Devuelve grupos duplicados de carpetas y seguimientos, sin modificar la biblioteca."""
    identity_loader = identity_loader or _folder_identity
    folder_records = [record for folder in (folders or [])
                      if (record := _folder_record(folder, identity_loader))]
    tracking_records = [record for entry in (tracked or [])
                        if (record := _tracking_record(entry))]
    groups = _groups(folder_records, "folders") + _groups(tracking_records, "tracking")
    groups.sort(key=lambda group: (group["confidence"] != "exact", group["scope"],
                                   group["records"][0]["name"].casefold()))
    return {
        "groups": groups,
        "count": len(groups),
        "checked": {"folders": len(folder_records), "tracking": len(tracking_records)},
    }
