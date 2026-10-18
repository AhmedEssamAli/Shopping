"""Instashop shops (e.g. Mahmoud ElFar) read from the rendered page in your Shopping Chrome.

Why the page and not the API: Instashop's product API answers with AES-encrypted bodies
and needs a reCAPTCHA token per request. shopper does not break either. It reads the
same tiles you see on screen.

Verified from a home-PC capture (2026-10-18): each product is an ``app-product`` with
``.product-title``, ``.product-packaging-string`` (size), ``.price-txt`` (EGP price) and a
``/product/<id>`` link. Not yet verified: that typing in the shop's search box
(``input[formcontrolname=query]``) lists matching products as the same tiles.
"""
from __future__ import annotations

import re

from ..browser import BrowserSession
from ..matching import has_word, tokens
from ..models import Offer
from .base import Adapter

BASE = "https://instashop.com/en-eg/client"

EXTRACT_JS = """() => [...document.querySelectorAll('app-product')].map(el => {
    const t = s => (el.querySelector(s)?.textContent || '').trim();
    const link = el.querySelector('a[href*="/product/"]');
    return {title: t('.product-title'), size: t('.product-packaging-string'),
            price: t('.price-txt'), was: t('.old-price, .price-before, .strike-price, s, del'),
            href: link ? link.href : '', id: link ? link.getAttribute('href').split('/product/')[1] : ''};
  }).filter(x => x.title && x.price)"""

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _price(text: str) -> float | None:
    m = _NUM.search((text or "").replace(",", ""))
    return float(m.group()) if m else None


class InstashopAdapter(Adapter):
    needs_browser = True

    def __init__(self, name: str, label: str, slug: str, session: BrowserSession, wait_s: float = 10):
        self.name, self.label, self.slug = name, label, slug
        self.session = session
        self.wait_s = wait_s
        self.login_url = f"{BASE}/{slug}"

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        page = await self.session.new_page()
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=45000)
            box = page.locator("input[formcontrolname=query]").first
            await box.wait_for(timeout=20000)
            await box.fill(query)
            await box.press("Enter")
            want = tokens(query)
            rows: list[dict] = []
            for _ in range(int(self.wait_s * 2)):  # wait until the tiles reflect the query
                await page.wait_for_timeout(500)
                rows = await page.evaluate(EXTRACT_JS)
                if any(any(has_word(w, tokens(r["title"])) for w in want) for r in rows):
                    break
        finally:
            await page.close()
        return self.parse(rows)[:limit]

    def parse(self, rows: list[dict]) -> list[Offer]:
        out, seen = [], set()
        for r in rows:
            price = _price(r.get("price", ""))
            if price is None or (r.get("id"), price) in seen:
                continue
            seen.add((r.get("id"), price))
            title = " ".join(r["title"].split())
            size = " ".join((r.get("size") or "").split())
            was = _price(r.get("was", ""))
            out.append(Offer(
                platform=self.name, name=f"{title} - {size}" if size else title, price=price,
                regular_price=was if was and was > price else None,
                url=r.get("href") or self.login_url, sku=r.get("id", ""), cart_ref=r.get("id", ""),
            ))
        return out
