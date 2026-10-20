"""Learns from your answers which matches are the right product.

Every "Right product? Yes / No", every "This one" and every "not this" is a labelled
example: (request, offer) -> right or wrong. A logistic regression over a few match
features is updated online with each answer (no library, no training run), and stored in
the local SQLite file with the examples, so it can be retrained or inspected later.

How it is used:
- Each line of a result shows a confidence (the model's probability that the chosen offer
  is right): high, medium or low, so you see where the search may have missed.
- Once it has MIN_ANSWERS answers, it also drops candidates it rates below DROP_BELOW
  before the cheapest exact match is chosen, so a wrong product that keeps coming back
  stops winning on price.
Before that, it only watches and reports "learning".
"""
from __future__ import annotations

import json
import math
import sqlite3

from .matching import VARIANT_WORDS, has_word, score, tokens
from .models import ItemRequest, Offer, now_utc

FEATURES = ("bias", "coverage", "exact", "score", "extra_words", "variant_words", "size_same", "size_unknown",
            "first_word", "price_vs_cheapest", "avoided")
MIN_ANSWERS = 20
DROP_BELOW = 0.25
LEARNING_RATE = 0.3
L2 = 0.001
# A sensible start, so the confidence means something from the first comparison.
PRIOR = {"bias": -1.0, "coverage": 2.0, "exact": 1.5, "score": 1.0, "extra_words": -0.5, "variant_words": -1.5,
         "size_same": 1.0, "size_unknown": -0.3, "first_word": 0.5, "price_vs_cheapest": -0.3, "avoided": -1.5}

SCHEMA = """
CREATE TABLE IF NOT EXISTS model (name TEXT PRIMARY KEY, weights TEXT, answers INTEGER);
CREATE TABLE IF NOT EXISTS examples (at TEXT, request TEXT, platform TEXT, offer TEXT, features TEXT, label INTEGER);
"""


def features(request: ItemRequest, offer: Offer, cheapest: float | None = None,
             avoid: frozenset[str] = frozenset()) -> dict[str, float]:
    req, off = tokens(request.query), tokens(offer.name)
    m = score(request, offer, avoid)
    extras = [w for w in off if not has_word(w, req)]
    want, got = request.size, offer.size
    ratio = offer.price / cheapest if cheapest else 1.0
    return {
        "bias": 1.0,
        "coverage": sum(has_word(w, off) for w in req) / len(req) if req else 0.0,
        "exact": 1.0 if m.exact else 0.0,
        "score": m.score / 100,
        "extra_words": min(len(extras), 6) / 6,
        "variant_words": min(sum(w in VARIANT_WORDS for w in extras), 3) / 3,
        "size_same": 1.0 if want and got and want.unit == got.unit and abs(want.total - got.total) <= 0.02 * want.total else 0.0,
        "size_unknown": 1.0 if want and not got else 0.0,
        "first_word": 1.0 if req and has_word(req[0], off) else 0.0,
        "price_vs_cheapest": min(math.log(max(ratio, 1.0)), 2.0) / 2,
        "avoided": 1.0 if any(w in avoid for w in extras) else 0.0,
    }


class Learner:
    def __init__(self, db: sqlite3.Connection, name: str = "match"):
        self.db, self.name = db, name
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT weights, answers FROM model WHERE name=?", (name,)).fetchone()
        self.w = {**PRIOR, **(json.loads(row[0]) if row else {})}
        self.answers = row[1] if row else 0

    @property
    def active(self) -> bool:
        return self.answers >= MIN_ANSWERS

    def probability(self, x: dict[str, float]) -> float:
        z = sum(self.w.get(k, 0.0) * v for k, v in x.items())
        return 1 / (1 + math.exp(-max(min(z, 30), -30)))

    def learn(self, request: ItemRequest, offer: Offer, right: bool, cheapest: float | None = None,
              avoid: frozenset[str] = frozenset()) -> None:
        x = features(request, offer, cheapest, avoid)
        err = (1.0 if right else 0.0) - self.probability(x)
        for k, v in x.items():
            self.w[k] = self.w.get(k, 0.0) + LEARNING_RATE * (err * v - L2 * (self.w.get(k, 0.0) - PRIOR.get(k, 0.0)))
        self.answers += 1
        self.db.execute("INSERT INTO examples VALUES (?,?,?,?,?,?)", (now_utc().isoformat(), request.query, offer.platform,
                                                                       offer.name, json.dumps(x), int(right)))
        self.db.execute("INSERT INTO model VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET weights=excluded.weights, "
                        "answers=excluded.answers", (self.name, json.dumps(self.w), self.answers))
        self.db.commit()

    def keep(self, request: ItemRequest, offers: list[Offer], avoid: frozenset[str] = frozenset()):
        """A filter for best_match: once trained, drop offers the model thinks are wrong."""
        if not self.active:
            return None
        cheapest = min((o.price for o in offers), default=None)
        return lambda o: self.probability(features(request, o, cheapest, avoid)) >= DROP_BELOW

    def confidence(self, request: ItemRequest, offer: Offer, pool: list[Offer], avoid: frozenset[str] = frozenset()) -> float:
        cheapest = min((o.price for o in pool), default=None)
        return self.probability(features(request, offer, cheapest, avoid))


def confidence_label(p: float) -> str:
    return "high" if p >= 0.75 else "medium" if p >= 0.5 else "low"
