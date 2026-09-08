"""
lightning-ocr · Persistent SQLite job history
Survives container restarts via mounted /data volume.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings


DB_PATH = Path(settings.DB_PATH)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 created_at REAL NOT NULL,
 backend_id TEXT NOT NULL,
 mode TEXT NOT NULL,
 filename TEXT,
 text_result TEXT,
 error TEXT,
 duration_ms INTEGER,
 meta TEXT -- JSON blob
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

# ── Security: Whitelist of allowed meta keys to prevent injection ─────────
ALLOWED_META_KEYS = {"attempted_backend", "used_backend", "fallback_chain", "gpu_layers"}

def sanitize_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize meta dict to only allow whitelisted keys."""
    if meta is None:
        return {}
    return {k: v for k, v in meta.items() if k in ALLOWED_META_KEYS}


@contextmanager
def _conn():
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(_SCHEMA)


def _parse_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Convert a Row to dict, parsing meta JSON."""
    if row is None:
        return None
    d = dict(row)
    meta_raw = d.get("meta")
    if isinstance(meta_raw, str):
        try:
            d["meta"] = json.loads(meta_raw)
        except (json.JSONDecodeError, TypeError):
            d["meta"] = {}
    return d


def _save_job_sync(
    backend_id: str,
    mode: str,
    filename: Optional[str],
    text_result: Optional[str],
    error: Optional[str],
    duration_ms: int,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    sanitized_meta = sanitize_meta(meta)
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO jobs
 (created_at, backend_id, mode, filename, text_result, error, duration_ms, meta)
 VALUES (?,?,?,?,?,?,?,?)""",
            (
                time.time(), backend_id, mode, filename,
                text_result, error, duration_ms,
                json.dumps(sanitized_meta),
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]


def _list_jobs_sync(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_parse_row(r) for r in rows]


def _get_job_sync(job_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _parse_row(row)


def _delete_job_sync(job_id: int) -> bool:
    with _conn() as con:
        cur = con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return cur.rowcount > 0


async def save_job(
    backend_id: str,
    mode: str,
    filename: Optional[str],
    text_result: Optional[str],
    error: Optional[str],
    duration_ms: int,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    return await asyncio.to_thread(
        _save_job_sync, backend_id, mode, filename,
        text_result, error, duration_ms, meta,
    )


async def list_jobs(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_list_jobs_sync, limit, offset)


async def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_get_job_sync, job_id)


async def delete_job(job_id: int) -> bool:
    return await asyncio.to_thread(_delete_job_sync, job_id)


init_db()