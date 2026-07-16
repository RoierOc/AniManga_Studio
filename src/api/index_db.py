#!/usr/bin/env python3
"""
On-disk index (SQLite) for expensive per-series filesystem measurements.

Both /api/library and /api/storage used to walk MANGA_DIR / UPSCALED_DIR / the
anime folder on every request (recursive byte sums + per-file globs). With a big
library that got slow. This memoizes each series' measurement keyed by its folder
mtime, so a series is only re-scanned when its contents actually change (manga
chapters are flat files inside the series folder, so adding/removing a page bumps
the folder mtime — a reliable, cheap invalidation signal).

The DB lives at DATA_ROOT/index.db (gitignored, like the rest of data/). It is a
pure cache: deleting it just forces a one-time rescan.
"""

import json
import os
import sqlite3
import threading

from api.runtime import DATA_ROOT

_DB_PATH = os.path.join(str(DATA_ROOT), "index.db")
_lock = threading.Lock()
_conn = None


def _conn_get():
    global _conn
    if _conn is None:
        os.makedirs(str(DATA_ROOT), exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        # Esto es un CACHÉ, no la fuente de verdad (ver el docstring del módulo): si un corte
        # se lleva las últimas filas, sólo se vuelve a medir. Con la durabilidad por defecto
        # cada commit hace fsync y cuesta **9 ms sobre ext4** — MEDIDO: memoizar las 1861
        # páginas de un manga eran 16,8 s SÓLO de commits, más que la medición en sí. Con WAL +
        # synchronous=NORMAL baja a 0,06 ms (0,1 s en total), 150x.
        # OJO al medir esto: en tmpfs (/tmp) un commit cuesta 0,02 ms y no se ve el problema.
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS measure ("
            "  root TEXT NOT NULL, name TEXT NOT NULL, mtime REAL,"
            "  bytes INTEGER, chapters INTEGER, extra TEXT,"
            "  PRIMARY KEY (root, name))"
        )
        _conn.commit()
    return _conn


def cached_measure(root, name, path, fn):
    """Return (bytes, chapters, extra_dict) for a series folder, measuring only if
    its mtime changed since the cached row.

    `fn(path)` performs the real (expensive) measurement and returns
    (bytes, chapters, extra_dict).
    """
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return 0, 0, {}

    with _lock:
        row = _conn_get().execute(
            "SELECT mtime, bytes, chapters, extra FROM measure WHERE root=? AND name=?",
            (root, name),
        ).fetchone()

    if row is not None and abs((row[0] or -1) - mt) < 1e-6:
        try:
            return row[1], row[2], json.loads(row[3] or "{}")
        except Exception:
            pass  # corrupt row → fall through and re-measure

    b, ch, extra = fn(path)
    with _lock:
        c = _conn_get()
        c.execute(
            "INSERT OR REPLACE INTO measure (root, name, mtime, bytes, chapters, extra) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (root, name, mt, int(b or 0), int(ch or 0), json.dumps(extra or {})),
        )
        c.commit()
    return b, ch, extra or {}


def prune(root, valid_names):
    """Drop cached rows for series that no longer exist on disk (freed space, renamed…)."""
    valid = set(valid_names)
    with _lock:
        c = _conn_get()
        rows = c.execute("SELECT name FROM measure WHERE root=?", (root,)).fetchall()
        gone = [r[0] for r in rows if r[0] not in valid]
        for n in gone:
            c.execute("DELETE FROM measure WHERE root=? AND name=?", (root, n))
        if gone:
            c.commit()
    return len(gone)
