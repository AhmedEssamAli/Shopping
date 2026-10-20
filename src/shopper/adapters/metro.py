"""Metro Markets' own website (metro-markets.com), read from its server-rendered search page.

Verified from this machine (2026-10-20): ``GET /search?key=<words>`` returns HTML with up to
12 ``div.product-card`` per page, each with a ``/product/<slug>/<id>`` link, ``h5`` (name and
size), ``p.after`` (price, "159 LE") and ``p.before`` (old price, often empty). The site
matches all words, so "juhayna milk" can return nothing while "juhayna" returns plenty;
shopper then also searches the first word and lets the matcher pick.
Not verified: whether these are the prices of the branch that delivers to your address
(the site has a location selector), and the delivery fee. Metro is also on talabat.
"""
from __future__ import annotations

import html
import re

import httpx

from ..models import Offer
from .base import Adapter

BASE = "https://www.metro-markets.com"
_CARD = re.compile(
    r'<div class="product-card card" data-id="(\d+)".*?<a href="([^"]+)".*?<h5>(.*?)</h5>'
    r'.*?<p class="after">(.*?)</p>\s*<p class="before">(.*?)</p>', re.S)
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _price(text: str) -> float | None:
    m = _NUM.search(text.replace(",", ""))
    return float(m.group()) if m else None


class MetroAdapter(Adapter):
    name = "metro_markets"
    label = "Metro Markets"
    login_url = BASE

    def __init__(self, name: str = "metro_markets", label: str = "Metro Markets"):
        self.name, self.label = name, label
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=25, follow_redirects=True,
                                             headers={"User-Agent": "Mozilla/5.0 (shopper personal price check)"})
        return self._client

    async def _page(self, words: str) -> list[Offer]:
        r = await self._http().get(f"{BASE}/search", params={"key": words})
        r.raise_for_status()
        return self.parse(r.text)

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        offers = await self._page(query)
        first = query.split()[0] if query.split() else ""
        if len(offers) < 3 and first and first.lower() != query.lower():
            seen = {o.sku for o in offers}
            offers += [o for o in await self._page(first) if o.sku not in seen]
        return offers[: limit * 2]

    def parse(self, page: str) -> list[Offer]:
        out = []
        for pid, url, name, now, was in _CARD.findall(page):
            price = _price(html.unescape(now))
            if price is None:
                continue
            old = _price(html.unescape(was))
            out.append(Offer(self.name, " ".join(html.unescape(name).split()), price, html.unescape(url), sku=pid,
                             regular_price=old if old and old > price else None))
        return out

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
