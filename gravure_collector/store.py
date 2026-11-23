"""取得状況と重複除外のための SQLite ストア。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username   TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    since_id   TEXT
);
CREATE TABLE IF NOT EXISTS media (
    media_key  TEXT PRIMARY KEY,
    post_id    TEXT NOT NULL,
    username   TEXT NOT NULL,
    sha256     TEXT,
    path       TEXT,
    saved_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS media_sha256 ON media(sha256);
"""


class Store:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def get_account(self, username: str) -> tuple[str, str | None] | None:
        row = self.conn.execute(
            "SELECT user_id, since_id FROM accounts WHERE username = ?", (username.lower(),)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def save_account(self, username: str, user_id: str) -> None:
        self.conn.execute(
            "INSERT INTO accounts(username, user_id) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET user_id = excluded.user_id",
            (username.lower(), user_id),
        )
        self.conn.commit()

    def set_since_id(self, username: str, since_id: str) -> None:
        self.conn.execute(
            "UPDATE accounts SET since_id = ? WHERE username = ?", (since_id, username.lower())
        )
        self.conn.commit()

    def has_media(self, media_key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM media WHERE media_key = ?", (media_key,)
        ).fetchone() is not None

    def find_by_hash(self, sha256: str) -> str | None:
        row = self.conn.execute(
            "SELECT path FROM media WHERE sha256 = ? AND path IS NOT NULL LIMIT 1", (sha256,)
        ).fetchone()
        return row[0] if row else None

    def add_media(self, media_key: str, post_id: str, username: str,
                  sha256: str, path: str | None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO media(media_key, post_id, username, sha256, path) "
            "VALUES (?, ?, ?, ?, ?)",
            (media_key, post_id, username.lower(), sha256, path),
        )
        self.conn.commit()
