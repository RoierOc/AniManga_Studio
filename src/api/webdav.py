#!/usr/bin/env python3
"""
Mobile library — HTTP file server so phones/tablets can download CBZ/CBR directly.
Serves individual files and whole manga folders as ZIP archives.
"""

import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import zipfile
from pathlib import Path

from flask import Blueprint, after_this_request, jsonify, request, send_file

from api.platform import is_wsl as _is_wsl2, first_windows_user_dir

_SETTINGS_FILE = Path(__file__).resolve().parents[2] / "library_settings.json"

def _load_settings() -> dict:
    try:
        return json.loads(_SETTINGS_FILE.read_text())
    except Exception:
        return {}

def _save_settings(d: dict):
    _SETTINGS_FILE.write_text(json.dumps(d, indent=2))

def _default_export_dir() -> Path:
    """Default: Documents/Mangas on Windows, ~/MangaExports elsewhere."""
    user = first_windows_user_dir()
    if user:
        target = user / "Documents" / "Mangas"
        target.mkdir(parents=True, exist_ok=True)
        return target
    p = Path.home() / "MangaExports"
    p.mkdir(parents=True, exist_ok=True)
    return p

def get_export_dir() -> Path:
    s = _load_settings()
    raw = s.get("library_path", "")
    if raw:
        p = Path(raw)
        if p.exists():
            return p
    return _default_export_dir()

def _get_server_ip() -> str:
    if _is_wsl2():
        try:
            out = subprocess.check_output(
                ["/mnt/c/Windows/System32/ipconfig.exe"], timeout=4
            ).decode("utf-8", errors="replace")
            for pat in [r"192\.168\.\d+\.\d+", r"10\.\d+\.\d+\.\d+"]:
                m = re.search(pat, out)
                if m:
                    return m.group(0)
        except Exception:
            pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _get_wsl2_ip() -> str:
    try:
        import subprocess as sp
        out = sp.check_output(["ip", "addr", "show"], timeout=3).decode()
        for line in out.splitlines():
            m = re.search(r"inet (172\.\d+\.\d+\.\d+)/", line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""

def _list_items(directory: Path) -> dict:
    """Return folders (with CBZ/CBR inside) and root-level individual files."""
    if not directory.exists():
        return {"folders": [], "files": []}

    folders = []
    files = []

    for item in sorted(directory.iterdir(), key=lambda x: x.name.lower()):
        if item.is_dir() and not item.name.startswith("."):
            cbz = sorted(
                [f for f in item.iterdir() if f.is_file() and f.suffix.lower() in (".cbz", ".cbr")],
                key=lambda f: f.name.lower(),
            )
            if cbz:
                total_mb = round(sum(f.stat().st_size for f in cbz) / 1_048_576, 1)
                folders.append({
                    "name": item.name,
                    "count": len(cbz),
                    "total_mb": total_mb,
                    "files": [{"name": f.name, "size_mb": round(f.stat().st_size / 1_048_576, 1)} for f in cbz],
                })
        elif item.is_file() and item.suffix.lower() in (".cbz", ".cbr"):
            files.append({"name": item.name, "size_mb": round(item.stat().st_size / 1_048_576, 1)})

    return {"folders": folders, "files": files}


webdav_bp = Blueprint("webdav", __name__)


@webdav_bp.route("/status")
def webdav_status():
    export_dir = get_export_dir()
    lan_ip = _get_server_ip()
    wsl_ip = _get_wsl2_ip()
    items = _list_items(export_dir)
    return jsonify({
        "export_dir": str(export_dir),
        "server_ip": lan_ip,
        "wsl_ip": wsl_ip,
        "library_url_local": "http://localhost:5100/library",
        "library_url_phone": f"http://{lan_ip}:5100/library",
        "folders": items["folders"],
        "files": items["files"],
    })


@webdav_bp.route("/files/<path:filename>")
def download_file(filename):
    export_dir = get_export_dir().resolve()
    target = (export_dir / filename).resolve()
    try:
        target.relative_to(export_dir)
    except ValueError:
        return "Forbidden", 403
    if not target.exists() or not target.is_file():
        return "Not found", 404
    return send_file(str(target), as_attachment=True, download_name=target.name)


@webdav_bp.route("/folder/<path:foldername>")
def download_folder(foldername):
    """Stream a folder's CBZ/CBR files as a ZIP archive."""
    export_dir = get_export_dir().resolve()
    folder = (export_dir / foldername).resolve()
    try:
        folder.relative_to(export_dir)
    except ValueError:
        return "Forbidden", 403
    if not folder.is_dir():
        return "Not found", 404

    cbz_files = sorted(
        [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in (".cbz", ".cbr")],
        key=lambda f: f.name.lower(),
    )
    if not cbz_files:
        return "Empty folder", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in cbz_files:
            zf.write(f, f.name)
    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{folder.name}.zip",
    )


@webdav_bp.route("/all")
def download_all():
    """Stream all CBZ/CBR files across all manga folders as one ZIP archive."""
    export_dir = get_export_dir().resolve()
    all_files: list[tuple[Path, str]] = []

    for item in sorted(export_dir.iterdir(), key=lambda x: x.name.lower()):
        if item.is_dir() and not item.name.startswith("."):
            cbz = sorted(
                [f for f in item.iterdir() if f.is_file() and f.suffix.lower() in (".cbz", ".cbr")],
                key=lambda f: f.name.lower(),
            )
            for f in cbz:
                all_files.append((f, item.name + "/" + f.name))
        elif item.is_file() and item.suffix.lower() in (".cbz", ".cbr"):
            all_files.append((item, item.name))

    if not all_files:
        return "Empty library", 404

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
            for f_path, arc_name in all_files:
                zf.write(str(f_path), arc_name)
    except Exception as exc:
        os.unlink(tmp_path)
        return str(exc), 500

    @after_this_request
    def _cleanup(response):
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return response

    return send_file(
        tmp_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name="Biblioteca_Manga.zip",
    )


@webdav_bp.route("/save", methods=["POST"])
def save_to_library():
    from api.export import get_export_tasks
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    if not task_id:
        return jsonify({"error": "task_id required"}), 400
    task = get_export_tasks().get(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    tmp_path = task.get("tmp_path")
    filename = task.get("filename", "export.cbz")
    if not tmp_path or not Path(tmp_path).exists():
        return jsonify({"error": "file missing"}), 404

    # Save inside a subfolder named after the manga title
    title = task.get("title", "").strip() or Path(filename).stem
    export_dir = get_export_dir()
    dest_dir = export_dir / title
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        counter += 1
    shutil.move(tmp_path, dest)
    return jsonify({"saved": True, "filename": dest.name, "path": str(dest)})


@webdav_bp.route("/delete", methods=["POST"])
def delete_library_file():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    folder = (data.get("folder") or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return jsonify({"error": "invalid name"}), 400
    export_dir = get_export_dir()
    if folder:
        if "/" in folder or "\\" in folder or folder.startswith("."):
            return jsonify({"error": "invalid folder"}), 400
        target = export_dir / folder / name
    else:
        target = export_dir / name
    if not target.exists():
        return jsonify({"error": "not found"}), 404
    target.unlink()
    # Remove folder if now empty
    if folder:
        parent = export_dir / folder
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    return jsonify({"deleted": True})
