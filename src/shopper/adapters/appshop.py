"""Shops that live only in a phone app (Breadfast, Rabbit): prices you type in.

Their websites carry no catalogue (checked again 2026-10-20: breadfast.com and
rabbitmart.com link only to the app stores) and their apps' data is not open, so shopper
keeps a small price book instead. You give it what the app shows, two ways:
- type lines ("Juhayna skimmed milk 1L = 54.95") in Telegram or on the home page, or
- send a screenshot of the app's search or category screen; Claude reads each product's
  name, size and price from the picture (direct Anthropic API, ANTHROPIC_API_KEY).
Comparisons then include those prices, labelled with their age. Prices older than
MAX_AGE_DAYS are left out, because app prices move.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from ..matching import has_word, tokens
from ..models import Offer, now_utc
from .base import Adapter

MAX_AGE_DAYS = 30
APP_LINKS = {"breadfast": "https://www.breadfast.com/", "rabbit": "https://www.rabbitmart.com/"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_prices (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, name TEXT, price REAL, at TEXT);
CREATE INDEX IF NOT EXISTS app_prices_platform ON app_prices(platform);
"""
_LINE = re.compile(r"^\s*(.+?)\s*(?:=|:|@|-{1,2}>?|—)\s*(?:EGP|LE|E£)?\s*(\d+(?:[.,]\d+)?)\s*(?:EGP|LE|E£|ج\.?م\.?)?\s*$", re.I)


def parse_prices(text: str) -> list[tuple[str, float]]:
    """"Juhayna skimmed milk 1L = 54.95" per line -> [(name, price)]; other lines are skipped."""
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out.append((" ".join(m.group(1).split()), float(m.group(2).replace(",", "."))))
    return out


class _Seen(BaseModel):
    name: str = Field(description="Product name as shown, with brand, type and size: 'Juhayna Skimmed Milk 1L'")
    price: float = Field(description="Price you pay now in EGP (the current price, not a crossed-out one)")


class _Screen(BaseModel):
    products: list[_Seen]


MODEL = "claude-opus-5-5"
_READ = ("This is a screenshot of an Egyptian grocery app ({shop}). List every product whose name and price are both "
         "fully visible. Copy the name as shown, adding the size or pack shown under it (1L, 6 x 1L, 500g). Use the "
         "current price in EGP; ignore crossed-out prices, delivery fees, cart totals and banners. Skip anything cut off. "
         "If the screen shows no products with prices, return an empty list.")


def read_screenshot(image: bytes, media_type: str, shop: str, api_key: str) -> list[tuple[str, float]]:
    """Names and prices from a screenshot of the app, read by Claude. Raises when no key is set."""
    import base64

    import anthropic

    if not api_key:
        raise RuntimeError("Reading screenshots needs ANTHROPIC_API_KEY in .env. You can type the prices instead.")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # a declined request is retried on a fallback model in the same call
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.standard_b64encode(image).decode()}},
            {"type": "text", "text": _READ.format(shop=shop)}]}],
        output_format=_Screen,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude could not read the screenshot (stop_reason={resp.stop_reason}).")
    return [(" ".join(p.name.split()), round(p.price, 2)) for p in resp.parsed_output.products
            if p.name.strip() and 0 < p.price < 100000]


class PriceBook:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.executescript(SCHEMA)

    def add(self, platform: str, entries: list[tuple[str, float]]) -> int:
        at = now_utc().isoformat()
        self.db.executemany("INSERT INTO app_prices (platform, name, price, at) VALUES (?,?,?,?)",
                            [(platform, name, price, at) for name, price in entries])
        self.db.commit()
        return len(entries)

    def latest(self, platform: str | None = None, max_age_days: int = MAX_AGE_DAYS) -> list[tuple[int, str, str, float, datetime]]:
        """The newest price per product name: (id, platform, name, price, at), newest first."""
        since = (now_utc() - timedelta(days=max_age_days)).isoformat()
        rows = self.db.execute(
            "SELECT id, platform, name, price, at FROM app_prices WHERE at >= ? AND (? IS NULL OR platform = ?) "
            "AND id IN (SELECT MAX(id) FROM app_prices GROUP BY platform, lower(name)) ORDER BY at DESC",
            (since, platform, platform))
        return [(i, p, n, pr, datetime.fromisoformat(a)) for i, p, n, pr, a in rows]

    def delete(self, entry_id: int) -> bool:
        cur = self.db.execute("DELETE FROM app_prices WHERE id = ?", (entry_id,))
        self.db.commit()
        return cur.rowcount > 0


class AppShopAdapter(Adapter):
    """Searches the price book for one app shop."""

    def __init__(self, name: str, label: str, book: PriceBook):
        self.name, self.label, self.book = name, label, book
        self.login_url = APP_LINKS.get(name, "")

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        want = tokens(query)
        out = []
        for entry_id, _, name, price, at in self.book.latest(self.name):
            words = tokens(name)
            if want and sum(has_word(w, words) for w in want) / len(want) >= 0.5:
                out.append(Offer(self.name, name, price, self.login_url, sku=f"book-{entry_id}", fetched_at=at))
        return out[:limit]
