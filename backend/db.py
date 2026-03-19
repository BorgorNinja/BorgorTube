"""
BorgorTube – SQLite persistence layer

Fix: use aiosqlite.connect() as a direct async context manager in each
function instead of the double-await pattern that caused:
  RuntimeError: threads can only be started once
"""

import os
import time
from typing import Optional

import aiosqlite

DB_PATH = os.environ.get("BORGORTUBE_DB", "borgortube.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_url    TEXT    NOT NULL,
    title        TEXT,
    thumbnail    TEXT,
    uploader     TEXT,
    uploader_url TEXT,
    duration     REAL,
    watched_at   REAL    NOT NULL DEFAULT (unixepoch()),
    watch_count  INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_watch_history_url ON watch_history(video_url);

CREATE TABLE IF NOT EXISTS cookies_store (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL DEFAULT 'default',
    content    TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch())
);
"""


async def _init_db(db: aiosqlite.Connection) -> None:
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()


# -- Watch history ------------------------------------------------------------

async def record_watch(
    video_url: str,
    title: str = "",
    thumbnail: str = "",
    uploader: str = "",
    uploader_url: str = "",
    duration: Optional[float] = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_db(db)
        await db.execute(
            """
            INSERT INTO watch_history
                (video_url, title, thumbnail, uploader, uploader_url, duration, watched_at, watch_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(video_url) DO UPDATE SET
                watched_at  = excluded.watched_at,
                watch_count = watch_count + 1,
                title       = excluded.title,
                thumbnail   = excluded.thumbnail
            """,
            (video_url, title, thumbnail, uploader, uploader_url, duration, time.time()),
        )
        await db.commit()


async def get_watch_history(limit: int = 50, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_db(db)
        cursor = await db.execute(
            "SELECT * FROM watch_history ORDER BY watched_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def delete_watch_entry(video_url: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_db(db)
        cursor = await db.execute(
            "DELETE FROM watch_history WHERE video_url = ?", (video_url,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_watch_history() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_db(db)
        cursor = await db.execute("DELETE FROM watch_history")
        await db.commit()
        return cursor.rowcount


# -- Cookie store -------------------------------------------------------------

async def save_cookies(content: str, label: str = "default") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_db(db)
        cursor = await db.execute(
            "INSERT INTO cookies_store (label, content, created_at) VALUES (?, ?, ?)",
            (label, content, time.time()),
        )
        await db.commit()
        return cursor.lastrowid


async def get_latest_cookies(label: str = "default") -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _init_db(db)
        cursor = await db.execute(
            "SELECT content FROM cookies_store WHERE label = ? ORDER BY created_at DESC LIMIT 1",
            (label,),
        )
        row = await cursor.fetchone()
        return row["content"] if row else None


async def write_cookies_file(path: str = "cookies.txt", label: str = "default") -> bool:
    content = await get_latest_cookies(label)
    if not content:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True
