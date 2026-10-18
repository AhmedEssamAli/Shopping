"""SQLite: price history, saved comparison pages, and each Telegram chat's list."""
from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from .models import ItemRequest, Offer, now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
  platform TEXT, sku TEXT, name TEXT, price REAL, regular_price REAL, url TEXT, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS offers_sku ON offers(platform, sku);
CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, created_at TEXT, html TEXT);
CREATE TABLE IF NOT EXISTS lists (chat_id INTEGER, pos INTEGER, query TEXT, qty INTEGER);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # One asyncio thread uses this; TestClient and uvicorn may hand calls to another thread.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

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
        self.db.execute("INSERT INTO results VALUES (?,?,?)", (rid, now_utc().isoformat(), html))
        self.db.commit()
        return rid

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
