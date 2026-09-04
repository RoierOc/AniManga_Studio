"""Medición y limpieza segura de cachés regenerables de AniManga Studio."""

import os
import shutil
from pathlib import Path

from api.observability import record_error
from api.runtime import DATA_ROOT


_PURGEABLE = {"image_cache", "export_temp"}


def _category_roots() -> dict[str, Path]:
    # Imports perezosos: storage se registra durante el arranque y no debe forzar ciclos entre los
    # módulos de exportación, proxy y biblioteca.
    from api import export as export_api
    from api import imgproxy

    return {
        "image_cache": Path(imgproxy._CACHE_DIR),
        "export_temp": Path(export_api._EXPORT_TMP),
    }


def _tree_bytes(root: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(root):
            try:
                if entry.is_dir(follow_symlinks=False):
                    total += _tree_bytes(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def summary() -> dict:
    """Devuelve bytes por caché, sin escanear las bibliotecas de contenido."""
    roots = _category_roots()
    image_cache = _tree_bytes(roots["image_cache"])
    export_temp = _tree_bytes(roots["export_temp"])
    protected = _protected_export_paths(roots["export_temp"])
    export_orphan = 0
    try:
        for child in roots["export_temp"].iterdir():
            if child.resolve() not in protected:
                export_orphan += _tree_bytes(child) if child.is_dir() else child.stat().st_size
    except OSError:
        pass
    return {
        "image_cache": image_cache,
        "export_temp": export_temp,
        "export_orphan": export_orphan,
        "total": image_cache + export_temp,
    }


def _protected_export_paths(root: Path) -> set[Path]:
    """Temporales que todavía representan una tarea visible para el usuario."""
    try:
        from api.export import get_export_tasks

        root = root.resolve()
        protected = set()
        for task in get_export_tasks().values():
            raw = task.get("tmp_path")
            if not raw:
                continue
            try:
                path = Path(raw).resolve()
                path.relative_to(root)
                protected.add(path)
            except (OSError, ValueError):
                pass
        return protected
    except Exception as exc:
        # Ante una lectura de estado imposible, no se borra ningún temporal: conservar datos es la
        # opción segura y el fallo queda registrado para diagnóstico.
        record_error("storage", exc, op="protected_export_paths")
        return {path for path in root.iterdir()} if root.is_dir() else set()


def _clear_image_index() -> None:
    try:
        from api import imgproxy

        with imgproxy._INDEX_LOCK:
            imgproxy._INDEX.clear()
    except Exception as exc:
        record_error("storage", exc, op="clear_image_index")


def _remove_children(root: Path, *, protected: set[Path] | None = None) -> int:
    protected = protected or set()
    freed = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    for child in children:
        try:
            if child.is_symlink():
                child.unlink()
                continue
            resolved = child.resolve()
            if resolved in protected:
                continue
            size = _tree_bytes(child) if child.is_dir() else child.stat().st_size
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            freed += size
        except OSError as exc:
            record_error("storage", exc, op="purge_cache", path=str(child))
    return freed


def purge(target: str) -> dict:
    """Limpia una categoría regenerable y devuelve los bytes liberados."""
    if target not in _PURGEABLE:
        raise ValueError("target inválido")
    root = _category_roots()[target]
    if target == "export_temp":
        freed = _remove_children(root, protected=_protected_export_paths(root))
    else:
        freed = _remove_children(root)
        _clear_image_index()
    return {"ok": True, "target": target, "freed": freed}
