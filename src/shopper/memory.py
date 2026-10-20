"""What shopper learns from your choices, kept in the local SQLite file.

- Picks: for a list line, the product you chose. On a platform where you picked it, that
  exact listing wins outright. Other platforms are matched against the line plus the pack
  and variant words of your choice ("Nescafe classic 200g" + "pouch"), and searched with it.
- Rejects: a listing you mark "not this" never comes back for that line.
- Word feedback: a pack or variant word you turn down twice and never pick (say "sachet")
  blocks exact matches on every line that does not ask for it.

Why memory, not a trained ranking model: retailers train rankers on millions of clicks.
One household makes a few dozen choices a month, too few to train on, but every one of
them is a direct answer, so storing it is exact from the first pick.
"""
from __future__ import annotations

import sqlite3
import zlib
from dataclasses import dataclass, field

from .matching import ACCEPT_SCORE, VARIANT_WORDS, score, tokens
from .models import ItemRequest, Offer, now_utc

# Words that name a pack type. Two offers that differ only here are different products to
# you (a jar is not a pouch), so they become separate cards, but neither word blocks a match.
FORM_WORDS = {
    "jar", "pouch", "sachet", "sachets", "stick", "sticks", "refill", "tin", "can", "cans",
    "bottle", "bottles", "box", "bag", "carton", "tube", "cup", "cups", "glass", "spray",
}
# Words that name a different kind of the same thing ("basmati" vs "egyptian" rice). They
# split cards, so a short line like "rice 5kg" asks which one, but never block a match.
KIND_WORDS = {
    "basmati", "jasmine", "egyptian", "brown", "parboiled", "sella", "wholegrain", "wholemeal", "multigrain", "decaf",
}
AVOID_AFTER = 2  # rejections of a word, with no pick, before it blocks exact matches

SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (key TEXT, platform TEXT, offer_id TEXT, name TEXT, canonical TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS rejects (key TEXT, platform TEXT, offer_id TEXT, name TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS word_feedback (word TEXT PRIMARY KEY, picked INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0);
"""


def request_key(request: ItemRequest) -> str:
    """Same line, same key: "2 Nescafé Classic 200 gm" and "nescafe classic 200g" agree."""
    size = request.size
    return " ".join(sorted(set(tokens(request.query)))) + (f"|{size.label()}" if size else "")


def offer_id(o: Offer) -> str:
    return o.sku or o.name  # Instashop popup items carry no id


def _short_hash(text: str) -> str:
    return f"{zlib.crc32(text.encode()) & 0xFFFFFFFF:08x}"


def offer_key(o: Offer) -> str:
    """Short stable id for one listing, for buttons and forms (Telegram allows 64 bytes)."""
    return _short_hash(f"{o.platform}|{offer_id(o)}")


def attributes(request: ItemRequest, o: Offer) -> tuple[str, frozenset[str], frozenset[str]]:
    """(size, pack words, variant and kind words the line did not ask for): what tells products apart."""
    words = set(tokens(o.name))
    asked = set(tokens(request.query))
    size = o.size.label() if o.size else ""
    return size, frozenset(words & FORM_WORDS), frozenset((words & (VARIANT_WORDS | KIND_WORDS)) - asked)


@dataclass
class ProductCard:
    """One distinct product for a line, with its listings across platforms."""
    size: str
    forms: frozenset[str]
    variants: frozenset[str]
    exact: bool
    offers: list[Offer] = field(default_factory=list)

    @property
    def label(self) -> str:
        parts = [self.size or "size not stated", " ".join(sorted(self.forms)) or "pack type not stated"]
        if self.variants:
            parts.append(" ".join(sorted(self.variants)))
        return ", ".join(parts)

    @property
    def words(self) -> frozenset[str]:
        return self.forms | self.variants

    @property
    def key(self) -> str:
        """Stable id: the same product gets the same key after cards are re-sorted."""
        return _short_hash(f"{self.size}|{' '.join(sorted(self.forms))}|{' '.join(sorted(self.variants))}")

    def cheapest(self) -> Offer:
        return min(self.offers, key=lambda o: o.price)

    def per_platform(self) -> dict[str, Offer]:
        out: dict[str, Offer] = {}
        for o in sorted(self.offers, key=lambda o: o.price):
            out.setdefault(o.platform, o)
        return out


def product_cards(request: ItemRequest, offers: list[Offer], avoid: frozenset[str] = frozenset(),
                  rejected: set[tuple[str, str]] | frozenset = frozenset(), limit: int = 6) -> list[ProductCard]:
    """Group acceptable offers into distinct products, exact ones first, cheapest first.
    Words you avoid still show, as non-exact cards; listings you rejected do not show."""
    cards: dict[tuple, ProductCard] = {}
    for o in offers:
        if (o.platform, offer_id(o)) in rejected:
            continue
        m = score(request, o, avoid)
        if not o.in_stock or m.score < ACCEPT_SCORE:
            continue
        size, forms, variants = attributes(request, o)
        key = (size, forms, variants)
        card = cards.setdefault(key, ProductCard(size, forms, variants, m.exact))
        card.exact = card.exact or m.exact
        card.offers.append(o)
    want = request.size
    def sensible(c: ProductCard) -> bool:  # an 18 g sachet is no answer to "200g"
        s = c.offers[0].size
        return c.exact or not (want and s and s.unit == want.unit and not 0.2 <= s.total / want.total <= 5)
    return sorted((c for c in cards.values() if sensible(c)), key=lambda c: (not c.exact, c.cheapest().price))[:limit]


@dataclass
class Recall:
    """Everything remembered for one line."""
    pins: dict[str, str]  # platform -> offer_id
    canonical: str | None  # the line plus the chosen pack/variant words
    rejected: set[tuple[str, str]]
    avoid: frozenset[str]


class Memory:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.executescript(SCHEMA)
        try:  # added after the first release: the line as you typed it
            self.db.execute("ALTER TABLE picks ADD COLUMN query TEXT")
        except sqlite3.OperationalError:
            pass

    def recall(self, request: ItemRequest) -> Recall:
        key = request_key(request)
        rows = self.db.execute("SELECT platform, offer_id, canonical FROM picks WHERE key=? ORDER BY at", (key,)).fetchall()
        rejected = {(p, i) for p, i in self.db.execute("SELECT platform, offer_id FROM rejects WHERE key=?", (key,))}
        return Recall({p: i for p, i, _ in rows}, rows[-1][2] if rows else None, rejected, self.avoid_words())

    def avoid_words(self) -> frozenset[str]:
        rows = self.db.execute("SELECT word FROM word_feedback WHERE picked=0 AND rejected>=?", (AVOID_AFTER,))
        return frozenset(w for (w,) in rows)

    def pick(self, request: ItemRequest, chosen: ProductCard, shown: list[ProductCard]) -> None:
        """Remember the chosen card; the exact cards you passed over teach which words you avoid."""
        key, at = request_key(request), now_utc().isoformat()
        canonical = " ".join([request.query, *sorted(chosen.words)])
        self.db.execute("DELETE FROM picks WHERE key=?", (key,))
        self.db.executemany("INSERT INTO picks (key, platform, offer_id, name, canonical, at, query) VALUES (?,?,?,?,?,?,?)",
                            [(key, p, offer_id(o), o.name, canonical, at, request.query)
                             for p, o in chosen.per_platform().items()])
        passed = {w for c in shown if c is not chosen and c.exact for w in c.words} - chosen.words
        self._words(chosen.words, "picked")
        self._words(passed, "rejected")
        self.db.commit()

    def reject(self, request: ItemRequest, o: Offer) -> None:
        key = request_key(request)
        self.db.execute("INSERT INTO rejects VALUES (?,?,?,?,?)", (key, o.platform, offer_id(o), o.name, now_utc().isoformat()))
        self.db.execute("DELETE FROM picks WHERE key=? AND platform=? AND offer_id=?", (key, o.platform, offer_id(o)))
        self.db.commit()

    def forget(self, request: ItemRequest) -> None:
        """Undo the pick for a line (rejects stay), so the cards show again."""
        self.db.execute("DELETE FROM picks WHERE key=?", (request_key(request),))
        self.db.commit()

    def chosen_lines(self, limit: int = 12) -> list[tuple[str, str]]:
        """(line as typed, chosen product words) for lines you chose a product for, newest first."""
        rows = self.db.execute("SELECT query, canonical, MAX(at) AS last FROM picks WHERE query IS NOT NULL "
                               "GROUP BY key ORDER BY last DESC LIMIT ?", (limit,))
        return [(q, c[len(q):].strip()) for q, c, _ in rows]

    def learned(self) -> dict[str, int]:
        """Counts for the page footer: what shopper knows so far."""
        n = lambda sql: self.db.execute(sql).fetchone()[0]
        return {"lines": n("SELECT COUNT(DISTINCT key) FROM picks"), "rejects": n("SELECT COUNT(*) FROM rejects"),
                "avoid": len(self.avoid_words())}

    def _words(self, words: set[str] | frozenset[str], column: str) -> None:
        for w in words:
            self.db.execute(f"INSERT INTO word_feedback (word, {column}) VALUES (?, 1) "
                            f"ON CONFLICT(word) DO UPDATE SET {column} = {column} + 1", (w,))
