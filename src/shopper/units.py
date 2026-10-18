"""Parse pack sizes from product names and normalise them to base units.

Base units: ml for volume, g for weight, pcs for counts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_UNIT_FACTORS = {
    "ml": ("ml", 1), "milliliter": ("ml", 1), "millilitre": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "ltr": ("ml", 1000),
    "liter": ("ml", 1000), "litre": ("ml", 1000), "liters": ("ml", 1000), "litres": ("ml", 1000),
    "g": ("g", 1), "gm": ("g", 1), "gr": ("g", 1), "gram": ("g", 1), "grams": ("g", 1),
    "kg": ("g", 1000), "kilo": ("g", 1000), "kilogram": ("g", 1000),
    "pcs": ("pcs", 1), "pc": ("pcs", 1), "piece": ("pcs", 1), "pieces": ("pcs", 1),
    "sachets": ("pcs", 1), "rolls": ("pcs", 1), "eggs": ("pcs", 1),
}
_UNIT_ALT = "|".join(sorted(map(re.escape, _UNIT_FACTORS), key=len, reverse=True))
_NUM = r"\d+(?:[.,]\d+)?"

# "6*1l", "6 x 1 L", "2x500g"
_MULTI = re.compile(rf"(\d+)\s*[x×*]\s*({_NUM})\s*({_UNIT_ALT})\b", re.I)
# "1 Liter", "200ml", "1.5l"
_SINGLE = re.compile(rf"({_NUM})\s*({_UNIT_ALT})\b", re.I)
# "Pack of 6", "6 pack", "x6", "6 pcs" is handled as a unit
_PACK = re.compile(r"pack\s*of\s*(\d+)|(\d+)\s*-?\s*pack\b|\bx\s*(\d+)\b", re.I)


@dataclass(frozen=True)
class Size:
    amount: float  # per unit, in base units
    unit: str  # ml | g | pcs
    pack: int = 1

    @property
    def total(self) -> float:
        return self.amount * self.pack

    def label(self) -> str:
        amt = f"{self.amount:g}{self.unit}"
        if self.unit == "ml" and self.amount >= 1000:
            amt = f"{self.amount / 1000:g}L"
        elif self.unit == "g" and self.amount >= 1000:
            amt = f"{self.amount / 1000:g}kg"
        return f"{self.pack}x{amt}" if self.pack > 1 else amt


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse_size(text: str) -> Size | None:
    """Return the pack size found in ``text``, or None."""
    t = text.replace("-", " ").replace("_", " ")
    m = _MULTI.search(t)
    if m:
        unit, factor = _UNIT_FACTORS[m.group(3).lower()]
        return Size(_to_float(m.group(2)) * factor, unit, int(m.group(1)))
    m = _SINGLE.search(t)
    if not m:
        return None
    unit, factor = _UNIT_FACTORS[m.group(2).lower()]
    amount = _to_float(m.group(1)) * factor
    pack = 1
    p = _PACK.search(t)
    if p and unit != "pcs":
        pack = int(next(g for g in p.groups() if g))
    return Size(amount, unit, pack)


def strip_sizes(text: str) -> str:
    """Remove size and pack tokens so name matching compares product words only."""
    t = _MULTI.sub(" ", text)
    t = _SINGLE.sub(" ", t)
    t = _PACK.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()
