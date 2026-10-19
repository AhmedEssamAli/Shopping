"""noon Egypt through the signed-in browser profile.

Verified from a capture on the home PC (2026-10-18):
- the catalogue API returns ``hits`` with offer_code, sku, brand, name, price,
  sale_price, pdp_url and is_buyable;
- add-to-cart is ``POST /_vs/nc/mp-noon-api-cart/cart/item/<offer_code>/<qty>?showCart``
  with body ``{}``.
- (2026-10-19, home-PC dump) the search page renders results into the page, not via a
  JSON call: each tile is ``[data-qa=plp-product-box]`` with ``plp-product-box-name``
  (full title in ``title``), ``plp-product-box-price`` and a ``/p/?o=<offer_code>`` link.
  Sponsored tiles (``widget_AdMultiProduct``) are skipped. JSON responses and embedded
  data are still read as a fallback.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote_plus

from ..browser import BrowserSession, dump_page, safe_evaluate
from ..matching import has_word, tokens
from ..models import Offer
from .base import Adapter

BASE = "https://www.noon.com/egypt-en"
TILES_JS = """() => [...document.querySelectorAll('[data-qa="plp-product-box"]')].map(el => {
    const a = el.querySelector('a[href*="/p/"]');
    const nameEl = el.querySelector('[data-qa="plp-product-box-name"]');
    const priceEl = el.querySelector('[data-qa="plp-product-box-price"]');
    const now = priceEl && (priceEl.querySelector('[class*="sellingPrice"] strong, strong') || {}).textContent;
    const was = priceEl && (priceEl.querySelector('[class*="oldPrice"]') || {}).textContent;
    return {href: a ? a.getAttribute('href') : '', name: nameEl ? (nameEl.getAttribute('title') || nameEl.textContent) : '',
            price: now || '', was: was || ''};
  }).filter(x => x.href && x.name && x.price)"""

CART_ADD_JS = """async ([code, qty]) => {
  const r = await fetch(`/_vs/nc/mp-noon-api-cart/cart/item/${code}/${qty}?showCart`, {
    method: 'POST', credentials: 'include', headers: {'content-type': 'application/json'}, body: '{}'});
  return r.status;
}"""


# Personalised feeds on every noon page also carry "hits"; never take them for results.
SKIP_URLS = ("recommendation", "personalized", "last-seen", "personalization", "user_search_history")


def relevance(hits: list[dict], query: str) -> float:
    """Share of hits whose name contains at least one word of the query."""
    want = tokens(query)
    if not want or not hits:
        return 0.0
    return sum(any(has_word(w, tokens(h.get("name") or "")) for w in want) for h in hits) / len(hits)


def tiles_to_hits(rows: list[dict]) -> list[dict]:
    """Rendered tiles -> the same dict shape as the catalogue API's ``hits``."""
    out = []
    for r in rows:
        m = re.match(r"/egypt-en(/[^?]*/p/)\?o=([A-Za-z0-9]+)", r["href"])
        if not m:
            continue
        num = lambda t: float(re.sub(r"[^\d.]", "", t)) if re.search(r"\d", t or "") else None
        now, was = num(r["price"]), num(r.get("was"))
        sku = m.group(1).rstrip("/").split("/")[-2]
        out.append({"offer_code": m.group(2), "sku": sku, "name": " ".join(r["name"].split()), "pdp_url": m.group(1),
                    "price": was if was and now and was > now else now, "sale_price": now if was and now and was > now else None,
                    "is_buyable": True})
    return out


def embedded_hits(html: str) -> list[dict]:
    """Product objects embedded in the page (server-rendered / Next.js flight data)."""
    text = html.replace('\\"', '"')
    out, seen = [], set()
    for m in re.finditer(r'"offer_code"\s*:\s*"([^"]+)"', text):
        if m.group(1) in seen:
            continue
        start = m.start()
        for _ in range(12):  # walk out to the enclosing object
            start = text.rfind("{", 0, start)
            if start < 0:
                break
            try:
                obj, _end = json.JSONDecoder().raw_decode(text[start:])
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("offer_code") == m.group(1) and ("price" in obj or "sale_price" in obj):
                seen.add(m.group(1))
                out.append(obj)
                break
    return out


def _hits(body) -> list[dict] | None:
    hits = body.get("hits") if isinstance(body, dict) else None
    if isinstance(hits, list) and hits and isinstance(hits[0], dict) and "offer_code" in hits[0]:
        return hits
    return None


class NoonAdapter(Adapter):
    name = "noon"
    label = "noon"
    needs_browser = True
    supports_cart_push = True
    login_url = f"{BASE}/"

    def __init__(self, session: BrowserSession, wait_s: float = 12):
        self.session = session
        self.wait_s = wait_s

    def search_url(self, query: str) -> str:
        return f"{BASE}/search/?q={quote_plus(query)}"

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        page = await self.session.new_page()
        found: list[list[dict]] = []
        pending: list[asyncio.Future] = []

        async def grab(resp) -> None:
            if "json" not in resp.headers.get("content-type", "") or any(s in resp.url for s in SKIP_URLS):
                return
            try:
                hits = _hits(await resp.json())
            except Exception:
                return
            if hits:
                found.append(hits)

        page.on("response", lambda r: pending.append(asyncio.ensure_future(grab(r))))
        html = ""
        try:
            await page.goto(self.search_url(query), wait_until="domcontentloaded", timeout=45000)
            for _ in range(int(self.wait_s * 2)):
                tiles = tiles_to_hits(await safe_evaluate(page, TILES_JS, []))
                if tiles:
                    found.append(tiles)
                if any(relevance(h, query) >= 0.3 for h in found):
                    break
                await page.wait_for_timeout(500)
            await asyncio.gather(*pending, return_exceptions=True)
            try:
                html = await page.content()
            except Exception:  # page still navigating: the tiles already read are what we have
                html = ""
        finally:
            await page.close()
        embedded = embedded_hits(html)
        if embedded:
            found.append(embedded)
        best = max(found, key=lambda h: relevance(h, query), default=None)
        if not best or relevance(best, query) == 0:
            dump = self._dump(query, html, found)
            raise RuntimeError(f"noon: no search results found. Saved the page to {dump} — send me that folder.")
        return self.parse(best)[:limit]

    def _dump(self, query: str, html: str, found: list[list[dict]]) -> Path:
        return dump_page(self.session.settings, self.name, query, html,
                         {"hit_sets": [len(h) for h in found], "names": [[x.get("name") for x in h[:5]] for h in found]})

    def parse(self, hits: list[dict]) -> list[Offer]:
        out = []
        for h in hits:
            sale, price = h.get("sale_price"), h.get("price")
            now = sale if sale else price
            if now is None:
                continue
            pdp = (h.get("pdp_url") or "").split("?")[0]
            out.append(Offer(
                platform=self.name, name=" ".join((h.get("name") or "").split()), price=float(now),
                regular_price=float(price) if sale and price else None,
                url=f"{BASE}{pdp}?o={h['offer_code']}", sku=h.get("sku", ""),
                in_stock=bool(h.get("is_buyable", True)), cart_ref=h["offer_code"],
            ))
        return out

    async def add_to_cart(self, lines: list[tuple[Offer, int]]) -> list[str]:
        page = await self.session.new_page()
        out = []
        try:
            await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=45000)
            for offer, qty in lines:
                status = await page.evaluate(CART_ADD_JS, [offer.cart_ref, qty])
                out.append(f"{'added' if status == 200 else f'FAILED (HTTP {status})'}: {qty} × {offer.name}")
        finally:
            await page.close()
        return out
