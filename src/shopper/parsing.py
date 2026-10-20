"""Turn a typed shopping list into ItemRequests.

With ANTHROPIC_API_KEY set, Claude normalises free text, Arabic and Franco-Arabic into
English search phrases ("2 لبن جهينة كامل الدسم لتر" -> 2 x "Juhayna full cream milk 1L").
Without it, a local parser splits lines and reads quantities.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from .entry import compose
from .models import ItemRequest
from .units import _SINGLE

log = logging.getLogger(__name__)
MODEL = "claude-opus-5-5"

# Commas split lines, except a comma between digits ("milk 1,5L").
_SPLIT = re.compile(r"[\n;،]+|(?<!\d),|,(?!\d)")
# The quantity must stand apart ("2 milk", "2x milk"), so "7up 1L" stays one product.
_LEAD_QTY = re.compile(r"^\s*(\d{1,3})(?:\s*[x×*]\s*|\s+)(\D.*)$", re.I)
_TRAIL_QTY = re.compile(r"^(.+?)\s*(?:[x×*]\s*(\d{1,3})|\((\d{1,3})\))\s*$", re.I)


def parse_local(text: str) -> list[ItemRequest]:
    items = []
    for raw in _SPLIT.split(text):
        line = raw.strip(" -•\t")
        if not line:
            continue
        qty = 1
        m = _LEAD_QTY.match(line)
        # "5kg rice" is a size, not a quantity
        if m and not _SINGLE.match(line):
            qty, line = int(m.group(1)), m.group(2).strip()
        else:
            t = _TRAIL_QTY.match(line)
            if t:
                line, qty = t.group(1).strip(), int(t.group(2) or t.group(3))
        items.append(ItemRequest(line, max(qty, 1)))
    return items


class _Item(BaseModel):
    item: str = Field(description="What the product is, in English, without brand, type or size: 'milk', 'rice', 'instant coffee'")
    brand: str = Field("", description="Brand if the user gave one, usual English spelling: 'Juhayna'. Empty if none")
    type: str = Field("", description="Variant, flavour or grade if given: 'skimmed', 'full cream', 'basmati'. Empty if none")
    size: str = Field("", description="Size of ONE piece if given, like '1L', '200ml', '500g', '5kg'. Empty if none")
    pieces: int = Field(1, ge=1, description="Pieces in one pack: 6 for a 6-pack, 30 for a tray of 30 eggs, else 1")
    qty: int = Field(1, ge=1, description="How many packs to buy")


class _List(BaseModel):
    items: list[_Item]


_SYSTEM = (
    "You convert a household shopping list for Cairo, Egypt into search phrases for Egyptian online stores "
    "(Amazon.eg, noon, talabat, Gourmet, Seoudi, Spinneys). The list may be English, Arabic or Franco-Arabic. "
    "Split each item into fields: item (what it is), brand, type (variant, flavour or grade), size of one piece, "
    "pieces in one pack, and how many packs to buy. Leave a field empty when the user did not give it; never "
    "invent a brand, type or size. Transliterate Egyptian brand names to their usual English spelling "
    "(جهينة -> Juhayna, المراعي -> Almarai). A size such as 5kg or 1 litre is the size, not the quantity. "
    "A 6-pack of 1 L milk is size '1L', pieces 6; a tray of 30 eggs is pieces 30 with no size."
)


def parse_with_claude(text: str, api_key: str) -> list[ItemRequest]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # a declined request is retried on a fallback model in the same call
        output_config={"effort": "low"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": text}],
        output_format=_List,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude did not return a list (stop_reason={resp.stop_reason})")
    return [e.request() for e in (compose(i.item, i.brand, i.type, i.size, i.pieces, i.qty)
                                   for i in resp.parsed_output.items) if e]


def parse_list(text: str, api_key: str = "") -> list[ItemRequest]:
    if api_key:
        try:
            return parse_with_claude(text, api_key)
        except Exception as e:  # network, auth, refusal: the local parser still works
            log.warning("Claude parsing failed, using local parser: %s", e)
    return parse_local(text)
