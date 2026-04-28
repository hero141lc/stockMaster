from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings


def _conn() -> sqlite3.Connection:
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup (
              dedup_key TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              created_at REAL NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dedup_created ON dedup(created_at)
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS job_state (
              k TEXT PRIMARY KEY,
              v TEXT NOT NULL
            )
            """
        )
        c.commit()


def get_state(key: str, default: str = "") -> str:
    with _conn() as c:
        row = c.execute("SELECT v FROM job_state WHERE k = ?", (key,)).fetchone()
        return str(row[0]) if row else default


def set_state(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO job_state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        c.commit()


def try_insert_dedup(kind: str, dedup_key: str, ttl_seconds: int = 86400 * 14) -> bool:
    """Return True if newly inserted (not duplicate)."""
    now = time.time()
    cutoff = now - ttl_seconds
    with _conn() as c:
        c.execute("DELETE FROM dedup WHERE created_at < ?", (cutoff,))
        try:
            c.execute(
                "INSERT INTO dedup(dedup_key, kind, created_at) VALUES (?,?,?)",
                (dedup_key, kind, now),
            )
            c.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def was_seen(kind: str, dedup_key: str) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM dedup WHERE dedup_key = ? AND kind = ? LIMIT 1",
            (dedup_key, kind),
        ).fetchone()
        return row is not None
