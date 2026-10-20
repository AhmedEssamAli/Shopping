"""SQLite: price history, saved comparison pages, and each Telegram chat's list."""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import ItemRequest, Offer, now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
  platform TEXT, sku TEXT, name TEXT, price REAL, regular_price REAL, url TEXT, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS offers_sku ON offers(platform, sku);
CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, created_at TEXT, html TEXT);
CREATE TABLE IF NOT EXISTS lists (chat_id INTEGER, pos INTEGER, query TEXT, qty INTEGER);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, query TEXT, target REAL,
  last_price REAL, last_platform TEXT, last_url TEXT, checked_at TEXT
);
"""


@dataclass
class Watch:
    id: int
    chat_id: int
    query: str
    target: float | None
    last_price: float | None
    last_platform: str | None
    last_url: str | None
    checked_at: str | None


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # One asyncio thread uses this; TestClient and uvicorn may hand calls to another thread.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        try:  # added after the first release
            self.db.execute("ALTER TABLE results ADD COLUMN summary TEXT")
        except sqlite3.OperationalError:
            pass

    def pref(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_pref(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO prefs VALUES (?,?)", (key, value))
        self.db.commit()

    def recent_names(self, word: str, limit: int = 400) -> list[str]:
        """Listing names seen before that contain ``word``, newest first (for suggestions)."""
        rows = self.db.execute("SELECT name FROM offers WHERE name LIKE ? GROUP BY name ORDER BY MAX(fetched_at) DESC LIMIT ?",
                               (f"%{word}%", limit))
        return [r[0] for r in rows]

    def record_offers(self, offers: list[Offer]) -> None:
        self.db.executemany(
            "INSERT INTO offers VALUES (?,?,?,?,?,?,?)",
            [(o.platform, o.sku, o.name, o.price, o.regular_price, o.url, o.fetched_at.isoformat()) for o in offers],
        )
        self.db.commit()

    def price_history(self, platform: str, sku: str) -> list[tuple[str, float]]:
        rows = self.db.execute(
            "SELECT fetched_at, price FROM offers WHERE platform=? AND sku=? ORDER BY fetched_at", (platform, sku)
        )
        return list(rows)

    def save_result(self, html: str) -> str:
        rid = secrets.token_urlsafe(6)
        self.db.execute("INSERT INTO results (id, created_at, html) VALUES (?,?,?)", (rid, now_utc().isoformat(), html))
        self.db.commit()
        return rid

    def update_result(self, rid: str, html: str, summary: str | None = None) -> None:
        self.db.execute("UPDATE results SET html=?, summary=COALESCE(?, summary) WHERE id=?", (html, summary, rid))
        self.db.commit()

    def recent_results(self, limit: int = 5) -> list[tuple[str, str, str]]:
        """(id, created_at, summary) of the latest comparisons, newest first."""
        rows = self.db.execute("SELECT id, created_at, COALESCE(summary, '') FROM results WHERE html != '' "
                               "ORDER BY created_at DESC LIMIT ?", (limit,))
        return list(rows)

    def merge_lists_into(self, owner: int) -> None:
        """One household list: older per-chat lists move to ``owner``, in order, after its own."""
        rows = self.db.execute("SELECT query, qty FROM lists ORDER BY chat_id != ?, chat_id, pos", (owner,)).fetchall()
        if not self.db.execute("SELECT 1 FROM lists WHERE chat_id != ? LIMIT 1", (owner,)).fetchone():
            return
        self.db.execute("DELETE FROM lists")
        seen, keep = set(), []
        for q, n in rows:  # the same line from two chats once
            if q.lower() not in seen:
                seen.add(q.lower())
                keep.append((q, n))
        self.db.executemany("INSERT INTO lists VALUES (?,?,?,?)", [(owner, i, q, n) for i, (q, n) in enumerate(keep)])
        self.db.commit()

    def get_result(self, rid: str) -> str | None:
        row = self.db.execute("SELECT html FROM results WHERE id=?", (rid,)).fetchone()
        return row[0] if row else None

    def get_list(self, chat_id: int) -> list[ItemRequest]:
        rows = self.db.execute("SELECT query, qty FROM lists WHERE chat_id=? ORDER BY pos", (chat_id,))
        return [ItemRequest(q, n) for q, n in rows]

    def add_to_list(self, chat_id: int, items: list[ItemRequest]) -> None:
        start = len(self.get_list(chat_id))
        self.db.executemany(
            "INSERT INTO lists VALUES (?,?,?,?)",
            [(chat_id, start + i, it.query, it.qty) for i, it in enumerate(items)],
        )
        self.db.commit()

    def remove_from_list(self, chat_id: int, index: int) -> bool:
        items = self.get_list(chat_id)
        if not 0 <= index < len(items):
            return False
        del items[index]
        self.clear_list(chat_id)
        self.add_to_list(chat_id, items)
        return True

    def clear_list(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM lists WHERE chat_id=?", (chat_id,))
        self.db.commit()

    def add_watch(self, chat_id: int, query: str, target: float | None) -> int:
        cur = self.db.execute("INSERT INTO watches (chat_id, query, target) VALUES (?,?,?)", (chat_id, query, target))
        self.db.commit()
        return cur.lastrowid

    def list_watches(self, chat_id: int | None = None) -> list[Watch]:
        sql = "SELECT id, chat_id, query, target, last_price, last_platform, last_url, checked_at FROM watches"
        rows = self.db.execute(sql + (" WHERE chat_id=? ORDER BY id" if chat_id is not None else " ORDER BY id"),
                               (chat_id,) if chat_id is not None else ())
        return [Watch(*r) for r in rows]

    def update_watch(self, watch_id: int, price: float, platform: str, url: str) -> None:
        self.db.execute("UPDATE watches SET last_price=?, last_platform=?, last_url=?, checked_at=? WHERE id=?",
                        (price, platform, url, now_utc().isoformat(), watch_id))
        self.db.commit()

    def delete_watch(self, chat_id: int, watch_id: int) -> bool:
        cur = self.db.execute("DELETE FROM watches WHERE chat_id=? AND id=?", (chat_id, watch_id))
        self.db.commit()
        return cur.rowcount > 0
