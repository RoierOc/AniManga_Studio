#!/usr/bin/env python3
"""
On-disk store (SQLite) for per-chapter source assignments — "Fuentes por capítulo".

Historically a manga had exactly one active source, recorded as a single object
(`recommended_source` or `sourceId/mangaId`) inside `.source_meta.json`. This module
lets each chapter of a manga be pinned to its own source (e.g. chapters 1-32 from
one scan, the rest from another), without touching that legacy file.

Resolution is layered and non-destructive:
  1. A row here for (title, chapter_norm) wins.
  2. No row -> caller falls back to the legacy `.source_meta.json` fields (unchanged
     behaviour for every manga until the user explicitly assigns something).
  3. Chapters already downloaded to disk are always readable regardless of this
     table — it only decides where *missing* chapters get pulled from.

The DB lives at DATA_ROOT/versions.db (gitignored, like index.db). It is not a
cache: rows are user/job intent and persist until explicitly cleared.
"""

import os
import sqlite3
import threading
import time

from api.runtime import DATA_ROOT

_DB_PATH = os.path.join(str(DATA_ROOT), "versions.db")
_lock = threading.Lock()
_conn = None


def _conn_get():
    global _conn
    if _conn is None:
        os.makedirs(str(DATA_ROOT), exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS chapter_sources ("
            "  title TEXT NOT NULL, chapter_norm TEXT NOT NULL,"
            "  source_kind TEXT NOT NULL, source_id TEXT, manga_id TEXT,"
            "  source_name TEXT, source_lang TEXT,"
            "  assigned_at REAL NOT NULL, assigned_by TEXT NOT NULL,"
            "  PRIMARY KEY (title, chapter_norm))"
        )
        _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cs_source "
            "ON chapter_sources(title, source_kind, manga_id)"
        )
        _conn.commit()
    return _conn


def _row_to_source(row):
    return {
        "sourceKind": row[0],
        "sourceId": row[1],
        "mangaId": row[2],
        "sourceName": row[3],
        "sourceLang": row[4],
        "assignedAt": row[5],
        "assignedBy": row[6],
    }


def get_assigned_map(title):
    """{chapter_norm: {sourceKind, sourceId, mangaId, sourceName, sourceLang,
    assignedAt, assignedBy}} for every chapter explicitly assigned for `title`."""
    with _lock:
        rows = _conn_get().execute(
            "SELECT chapter_norm, source_kind, source_id, manga_id, source_name, "
            "source_lang, assigned_at, assigned_by FROM chapter_sources WHERE title=?",
            (title,),
        ).fetchall()
    return {r[0]: _row_to_source(r[1:]) for r in rows}


def get_assignment(title, chapter_norm):
    """Explicit assignment for one chapter, or None if none exists (caller should
    fall back to legacy .source_meta.json in that case)."""
    with _lock:
        row = _conn_get().execute(
            "SELECT source_kind, source_id, manga_id, source_name, source_lang, "
            "assigned_at, assigned_by FROM chapter_sources WHERE title=? AND chapter_norm=?",
            (title, chapter_norm),
        ).fetchone()
    return _row_to_source(row) if row else None


def set_assignment_range(title, chapters, source, assigned_by="manual"):
    """Upsert (or clear, if source is None) the source for each chapter_norm in
    `chapters` (an iterable of strings). `source` is
    {sourceKind, sourceId?, mangaId, sourceName?, sourceLang?} or None to clear.
    Returns the list of chapter_norms actually written/cleared."""
    chapters = list(dict.fromkeys(chapters))  # dedupe, keep order
    if not chapters:
        return []
    now = time.time()
    with _lock:
        c = _conn_get()
        if source is None:
            c.executemany(
                "DELETE FROM chapter_sources WHERE title=? AND chapter_norm=?",
                [(title, ch) for ch in chapters],
            )
        else:
            c.executemany(
                "INSERT OR REPLACE INTO chapter_sources "
                "(title, chapter_norm, source_kind, source_id, manga_id, source_name, "
                "source_lang, assigned_at, assigned_by) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        title, ch,
                        source.get("sourceKind"), source.get("sourceId"),
                        source.get("mangaId"), source.get("sourceName"),
                        source.get("sourceLang"), now, assigned_by,
                    )
                    for ch in chapters
                ],
            )
        c.commit()
    return chapters


def clear_title(title):
    """Drop all chapter assignments for a manga (e.g. it was deleted from the library)."""
    with _lock:
        c = _conn_get()
        c.execute("DELETE FROM chapter_sources WHERE title=?", (title,))
        c.commit()
