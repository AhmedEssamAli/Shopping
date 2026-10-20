"""The item entry: what one line of your list means, field by field.

An item has six fields. Only the item is required.

  item     what it is                      milk, rice, eggs, instant coffee
  brand    the maker, if it matters        Juhayna, Al Doha, Nescafe
  type     variant, flavour or grade       skimmed, full cream, basmati, classic
  size     one piece                       1L, 200ml, 500g, 5kg
  pieces   pieces in one pack              6 (a 6-pack of 1L), 30 (a tray of eggs)
  qty      how many packs to buy           2

The fields are joined into the search phrase "Juhayna skimmed milk 6x1L", and read back
from any phrase, so a typed line, a pasted list, the item form and the Telegram steps all
mean the same thing. What you leave out is open: no brand means any brand. What you give
is required: a brand, a type or a size you name must be on the product for an exact match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ItemRequest
from .units import Size, _MULTI, _PACK, _SINGLE, parse_size

# Common brands in Egyptian grocery shops, to recognise a brand in a typed line. A brand you
# type in the brand field, or one you chose before, works whether it is listed or not.
BRANDS = [
    "Juhayna", "Almarai", "Lamar", "Domty", "Beyti", "Labanita", "Danone", "Nestle", "Nescafe", "Lipton",
    "Al Arosa", "El Arosa", "Abu Auf", "Al Doha", "El Doha", "Doha", "Al Ahram", "Panda", "Persil", "Ariel",
    "Tide", "Fairy", "Dettol", "Pampers", "Molped", "Always", "Molto", "Chipsy", "Pepsi", "Coca Cola",
    "Coca-Cola", "Schweppes", "President", "Kiri", "La Vache Qui Rit", "Heinz", "Americana", "Halwani",
    "El Maleka", "Italiano", "Regina", "Crystal", "Afia", "Sunshine", "Lino", "Isis", "Hayat", "Baraka",
    "Nestle Pure Life", "Aquafina", "Dasani", "Siwa", "Safi", "Galaxy", "Cadbury", "Kinder", "Nutella",
    "Bonjorno", "Koki", "Wadi Food", "Rauch", "Hero", "Vitrac", "Kellogg's", "Kelloggs", "Quaker",
    "Bel", "Puck", "Teama", "El Rashidi", "El Rashidy", "Faragello", "Sabahy", "Edita", "Tiger", "Todo",
    "Oreo", "Lu", "Head & Shoulders", "Pantene", "Dove", "Lux", "Lifebuoy", "Signal", "Colgate", "Clorox",
    "Harpic", "Downy", "Omo", "Bref", "Mr Muscle", "Finish", "Sunsilk", "Nivea", "Johnson's", "Molfix",
    "Lactel", "Activia", "Greenland", "Hellmann's", "Knorr", "Maggi", "Royal", "El Kebir", "Gourmet",
    "Seoudi", "Spinneys", "Carrefour", "Amazon Basics",
]

# Type words, longest first so "full cream" wins over "cream".
TYPES = [
    "lactose free", "full cream", "full fat", "half cream", "low fat", "fat free", "semi skimmed", "sugar free",
    "no sugar", "extra virgin", "whole wheat", "whole grain", "skimmed", "skim", "light", "zero", "diet",
    "decaf", "classic", "gold", "original", "basmati", "jasmine", "egyptian", "brown", "white", "parboiled",
    "organic", "unsalted", "salted", "plain", "chocolate", "strawberry", "vanilla", "banana", "mango",
    "caramel", "coconut", "almond", "oat", "soy", "spicy", "sweet", "sour", "cheese", "ketchup", "bbq",
    "chili", "lemon", "orange", "apple", "mint", "honey", "sensitive", "fresh", "frozen", "long life",
]


@dataclass
class Entry:
    item: str
    brand: str = ""
    type: str = ""
    size: str = ""
    pieces: int = 1
    qty: int = 1

    def phrase(self) -> str:
        """The search phrase: brand, type, item, then pieces x size."""
        words = [self.brand] if self.brand and self.brand.lower() not in self.item.lower() else []
        if self.type and self.type.lower() not in self.item.lower():
            words.append(self.type)
        words.append(self.item)
        if self.pieces > 1:
            words.append(f"{self.pieces}x{self.size}" if self.size else f"{self.pieces} pcs")
        elif self.size:
            words.append(self.size)
        return " ".join(w for w in words if w)

    def request(self) -> ItemRequest:
        return ItemRequest(self.phrase(), max(self.qty, 1))

    def summary(self) -> str:
        """One line for chat: "brand Juhayna · type skimmed · size 1L · pack of 6"; open fields read "any"."""
        parts = [f"brand {self.brand or 'any'}", f"type {self.type or 'any'}", f"size {self.size or 'any'}"]
        if self.pieces > 1:
            parts.append(f"pack of {self.pieces}")
        return " · ".join(parts)

    def fields(self) -> list[tuple[str, str]]:
        """What shopper understood, for display: [("Brand", "Juhayna"), ...]."""
        out = [("Item", self.item)]
        out += [(k, v) for k, v in (("Brand", self.brand), ("Type", self.type), ("Size", self.size)) if v]
        if self.pieces > 1:
            out.append(("Pack", f"{self.pieces} pieces"))
        out.append(("Buy", f"{self.qty}"))
        return out


def _clean(s: str) -> str:
    return " ".join(str(s or "").split()).strip(" ,-")


def _find(words: list[str], text: str) -> tuple[str, str]:
    """The first listed word or phrase found in ``text`` as whole words; (found as listed, text without it)."""
    for w in sorted(words, key=len, reverse=True):
        m = re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", text, re.I)
        if m:
            return w, _clean(text[:m.start()] + " " + text[m.end():])
    return "", text


def size_text(size: Size) -> str:
    """One piece, as written on shelves: 1L, 200ml, 5kg, 30 pcs."""
    one = Size(size.amount, size.unit)
    return f"{size.amount:g} pcs" if size.unit == "pcs" else one.label()


def read_entry(text: str, qty: int = 1, brands: list[str] | None = None) -> Entry:
    """Split a phrase into fields. "Juhayna skimmed milk 6x1L" -> brand, type, item, size 1L, pieces 6."""
    rest = _clean(text)
    size, pieces = "", 1
    found = parse_size(rest)
    if found:
        size, pieces = size_text(found), (found.pack if found.unit != "pcs" else 1)
        if found.unit == "pcs":
            pieces, size = int(found.amount), ""
        rest = _clean(_PACK.sub(" ", _SINGLE.sub(" ", _MULTI.sub(" ", rest))))
    brand, left = _find((brands or []) + BRANDS, rest)
    rest = left or rest  # "Nescafe 200g": the brand is the item too
    kind, left = _find(TYPES, rest)
    if left:  # "cheese" alone is the item, not a type
        rest = left
    else:
        kind = ""
    return Entry(rest or _clean(text), brand, kind.lower(), size, pieces, max(qty, 1))


def entry_of(request: ItemRequest, brands: list[str] | None = None) -> Entry:
    return read_entry(request.query, request.qty, brands)


def compose(item: str, brand: str = "", kind: str = "", size: str = "", pieces: str | int = "",
            qty: str | int = "") -> Entry | None:
    """One row of the item form. Pieces alone mean a count ("eggs", pieces 30 -> "eggs 30 pcs")."""
    item, brand, kind, size = (_clean(x) for x in (item, brand, kind, size))
    if not item:
        return None
    if size and re.fullmatch(r"\d+(?:[.,]\d+)?", size):
        size = ""  # a bare number is not a size; the form's unit list sends "1" + "L" as "1L"
    p = str(pieces or "").strip()
    try:
        n = max(int(str(qty).strip() or 1), 1)
    except ValueError:
        n = 1
    return Entry(item, brand, kind.lower(), size.replace(" ", ""), int(p) if p.isdigit() and int(p) > 1 else 1, n)


def suggest(names: list[str], item: str, limit: int = 6) -> dict[str, list[str]]:
    """Brands, types and sizes seen on shop listings for this item, most common first."""
    from collections import Counter

    words = [w for w in re.findall(r"\w+", item.lower()) if len(w) > 2]
    hits = [n for n in names if words and all(re.search(rf"\b{re.escape(w)}", n, re.I) for w in words)]
    out: dict[str, Counter] = {"brand": Counter(), "type": Counter(), "size": Counter()}
    for n in hits:
        e = read_entry(n)
        if e.brand:
            out["brand"][e.brand] += 1
        if e.type and e.type.lower() not in item.lower():
            out["type"][e.type] += 1
        if e.size:
            out["size"][e.size] += 1
    return {k: [v for v, _ in c.most_common(limit)] for k, c in out.items()}
