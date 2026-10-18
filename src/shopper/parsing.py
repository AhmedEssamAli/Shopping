"""Turn a typed shopping list into ItemRequests.

With ANTHROPIC_API_KEY set, Claude normalises free text, Arabic and Franco-Arabic into
English search phrases ("2 لبن جهينة كامل الدسم لتر" -> 2 x "Juhayna full cream milk 1L").
Without it, a local parser splits lines and reads quantities.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from .models import ItemRequest
from .units import _SINGLE

log = logging.getLogger(__name__)
MODEL = "claude-opus-5-5"

_SPLIT = re.compile(r"[\n;,،]+")
_LEAD_QTY = re.compile(r"^\s*(\d{1,3})\s*(?:[x×*]\s*)?(?=\D)(.+)$", re.I)
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
    query: str = Field(description="English search phrase: brand, product, variant, size. e.g. 'Juhayna full cream milk 1L'")
    qty: int = Field(ge=1, description="How many units to buy")


class _List(BaseModel):
    items: list[_Item]


_SYSTEM = (
    "You convert a household shopping list for Cairo, Egypt into search phrases for Egyptian online stores "
    "(Amazon.eg, noon, talabat, Gourmet, Seoudi, Spinneys). The list may be English, Arabic or Franco-Arabic. "
    "For each item return one concise English phrase with brand (if given), product, variant and size, and the "
    "quantity to buy. Keep the user's brand and size; do not invent a brand or size they did not give. "
    "Transliterate Egyptian brand names to their usual English spelling (جهينة -> Juhayna, المراعي -> Almarai). "
    "A size such as 5kg or 1 litre is part of the phrase, not the quantity."
)


def parse_with_claude(text: str, api_key: str) -> list[ItemRequest]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=_SYSTEM,
        messages=[{"role": "user", "content": text}],
        output_format=_List,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude did not return a list (stop_reason={resp.stop_reason})")
    return [ItemRequest(i.query.strip(), i.qty) for i in resp.parsed_output.items if i.query.strip()]


def parse_list(text: str, api_key: str = "") -> list[ItemRequest]:
    if api_key:
        try:
            return parse_with_claude(text, api_key)
        except Exception as e:  # network, auth, refusal: the local parser still works
            log.warning("Claude parsing failed, using local parser: %s", e)
    return parse_local(text)
