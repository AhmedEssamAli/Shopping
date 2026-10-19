"""Decide which offers are the item you asked for.

Rule: every word of the request must appear in the offer name (fuzzy), the size must
agree when you gave one, and the offer must not carry a variant word you did not ask
for ("chocolate", "lactose free", ...). Offers that pass all three are "exact".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from .models import ItemRequest, Match, Offer
from .units import Size, strip_sizes

STOPWORDS = {
    "the", "and", "with", "of", "for", "a", "an", "pack", "save", "le", "egp", "discount",
    "new", "offer", "pcs", "pc", "piece", "pieces",
}
# Words that turn a product into a different variant. If the offer has one the request
# does not, it is never an exact match.
VARIANT_WORDS = {
    "lactose", "free", "chocolate", "strawberry", "banana", "vanilla", "caramel", "mango",
    "light", "zero", "diet", "organic", "kids", "sugar", "unsweetened", "flavored",
    "flavoured", "coconut", "almond", "oat", "soy", "barista", "low", "mix", "spicy",
    "cream", "skimmed", "semi", "half", "full", "fat", "0",
    # flavours
    "vinegar", "chili", "chilli", "ranch", "tomato", "cheese", "bbq", "ketchup", "lemon",
    "onion", "sour", "hot", "sweet", "salted", "unsalted", "honey", "mint", "orange", "apple",
}
ACCEPT_SCORE = 75.0
_TOKEN_MATCH = 85


def _fold(text: str) -> str:
    """Lowercase and drop accents: "Nescafé" -> "nescafe"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    text = strip_sizes(_fold(text)).replace("%", " ")
    words = re.findall(r"[a-z0-9؀-ۿ]+", text)
    return [w for w in words if w not in STOPWORDS]


def has_word(word: str, pool: list[str]) -> bool:
    return any(w == word or fuzz.ratio(w, word) >= _TOKEN_MATCH for w in pool)


def _same_size(a: Size, b: Size) -> bool:
    return a.unit == b.unit and abs(a.total - b.total) <= 0.02 * max(a.total, b.total)


def score(request: ItemRequest, offer: Offer) -> Match:
    req = tokens(request.query)
    off = tokens(offer.name)
    if not req:
        return Match(request, offer, 0.0, False)
    covered = sum(has_word(w, off) for w in req) / len(req)
    extras = [w for w in off if not has_word(w, req)]
    variant_extras = [w for w in extras if w in VARIANT_WORDS]

    s = covered * 100 - 4 * len(extras) - 10 * len(variant_extras)
    want, got = request.size, offer.size
    size_ok = True
    if want:
        if got is None:
            s -= 10
            size_ok = False
        elif not _same_size(want, got):
            s -= 25
            size_ok = False
    # Extra generic words ("instant coffee", a brand you did not name) lower the score but
    # do not block exact; variant words do.
    exact = covered == 1 and size_ok and not variant_extras
    return Match(request, offer, max(s, 0.0), exact)


def best_match(request: ItemRequest, offers: list[Offer]) -> Match | None:
    """Best acceptable in-stock offer from one platform.

    Any exact match beats every close one, and the cheapest exact wins. Without an exact
    match, the closest-scoring offer wins, cheapest among equals.
    """
    cands = [m for m in (score(request, o) for o in offers if o.in_stock) if m.score >= ACCEPT_SCORE]
    if not cands:
        return None
    return min(cands, key=lambda m: (0, 0, m.offer.price) if m.exact else (1, -round(m.score, -1), m.offer.price))


@dataclass
class ValueTip:
    """Another pack size of the same product that costs less per litre, kg or piece."""
    offer: Offer
    unit_price: float
    baseline: float

    @property
    def saving_pct(self) -> float:
        return (1 - self.unit_price / self.baseline) * 100


VALUE_TIP_MIN_SAVING = 5.0  # percent
VALUE_TIP_MIN_SIZE_RATIO = 0.2  # alternative pack must be at least 1/5 of the chosen size


def value_tip(request: ItemRequest, chosen: Match | None, candidates: list[Offer]) -> ValueTip | None:
    """Cheapest-per-unit offer of the same product in a different size, if it saves enough."""
    if not chosen or not chosen.offer.unit_price or not chosen.offer.size:
        return None
    base, unit = chosen.offer.unit_price, chosen.offer.size.unit
    loose = ItemRequest(strip_sizes(request.query) or request.query, request.qty)
    best: ValueTip | None = None
    for o in candidates:
        up = o.unit_price
        if o is chosen.offer or not o.in_stock or up is None or o.size.unit != unit:
            continue
        if o.size.total < VALUE_TIP_MIN_SIZE_RATIO * chosen.offer.size.total:
            continue  # twenty 250 g packs are not a sensible swap for one 5 kg bag
        if up > base * (1 - VALUE_TIP_MIN_SAVING / 100) or not score(loose, o).exact:
            continue
        if best is None or up < best.unit_price:
            best = ValueTip(o, up, base)
    return best
