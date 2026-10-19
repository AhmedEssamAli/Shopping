"""Instashop shops (e.g. Mahmoud ElFar) read from the rendered page in your Shopping Chrome.

Why the page and not the API: Instashop's product API answers with AES-encrypted bodies
and needs a reCAPTCHA token per request. shopper does not break either. It reads the
same tiles you see on screen.

Verified from a home-PC capture (2026-10-18): each product is an ``app-product`` with
``.product-title``, ``.product-packaging-string`` (size), ``.price-txt`` (EGP price) and a
``/product/<id>`` link.

Search (from two failed-search dumps, 2026-10-19): the header box
``input[formcontrolname=query]`` ("Search for shops, categories or products") shows no
type-ahead list. Pressing Enter opens a results popup, ``app-search-page``, where each
product is an ``nb-list-item`` with ``h5`` (title), ``h6.packagingString`` (size) and
``.price``. The items carry no product link, so offers link to the shop page.
Assumption, not verified: the popup lists only this shop's products (it is opened from
the shop page and names no other shop).
"""
from __future__ import annotations

import re

from ..browser import BrowserSession, dump_page, safe_evaluate
from ..matching import has_word, tokens
from ..models import Offer
from .base import Adapter

BASE = "https://instashop.com/en-eg/client"

EXTRACT_JS = """() => {
  const txt = (el, s) => (el.querySelector(s)?.textContent || '').trim();
  const was = '.old-price, .price-before, .strike-price, s, del';
  // Search results popup opened by Enter.
  const popup = [...document.querySelectorAll('app-search-page nb-list-item')].map(el => {
    const link = el.querySelector('a[href*="/product/"]');
    return {title: txt(el, 'h5'), size: txt(el, '.packagingString'), price: txt(el, '.price'), was: txt(el, was),
            href: link ? link.href : '', id: link ? link.getAttribute('href').split('/product/')[1] : ''};
  });
  // Product tiles on shop and category pages: app-product, or the nearest ancestor of a
  // title that also holds a price.
  const tiles = [...new Set([...document.querySelectorAll('.product-title')].map(t =>
    t.closest('app-product') || (() => { let e = t; for (let i = 0; i < 6 && e; i++) {
      e = e.parentElement; if (e && e.querySelector('.price-txt')) return e; } return null; })()
  ).filter(Boolean))].map(el => {
    const link = el.querySelector('a[href*="/product/"]');
    return {title: txt(el, '.product-title'), size: txt(el, '.product-packaging-string'), price: txt(el, '.price-txt'),
            was: txt(el, was), href: link ? link.href : '', id: link ? link.getAttribute('href').split('/product/')[1] : ''};
  });
  return [...popup, ...tiles].filter(x => x.title && x.price);
}"""

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
        want = tokens(query)
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=45000)
            box = page.locator("input[formcontrolname=query]").first
            await box.wait_for(timeout=20000)
            await box.click()
            await box.press_sequentially(query, delay=40)
            await box.press("Enter")
            rows = await self._poll(page, want)
            if not rows:
                dump = dump_page(self.session.settings, self.name, query, await page.content(), {"url": page.url})
                raise RuntimeError(f"{self.label}: no products after searching. Saved the page to {dump} — send me that folder.")
        finally:
            await page.close()
        return self.parse(rows)[:limit]

    @staticmethod
    def _relevant(rows: list[dict], want: list[str]) -> bool:
        return any(any(has_word(w, tokens(r["title"])) for w in want) for r in rows)

    async def _poll(self, page, want: list[str]) -> list[dict]:
        """Wait until the results reflect the query, then once more for the list to fill."""
        rows: list[dict] = []
        for _ in range(int(self.wait_s * 2)):
            await page.wait_for_timeout(500)
            rows = await safe_evaluate(page, EXTRACT_JS, [])
            if self._relevant(rows, want):
                await page.wait_for_timeout(1000)
                return await safe_evaluate(page, EXTRACT_JS, rows)
        return rows

    def parse(self, rows: list[dict]) -> list[Offer]:
        out, seen = [], set()
        for r in rows:
            price = _price(r.get("price", ""))
            title = " ".join(r["title"].split())
            size = " ".join((r.get("size") or "").split())
            key = (r.get("id") or f"{title}|{size}", price)  # popup items have no id
            if price is None or key in seen:
                continue
            seen.add(key)
            was = _price(r.get("was", ""))
            out.append(Offer(
                platform=self.name, name=f"{title} - {size}" if size else title, price=price,
                regular_price=was if was and was > price else None,
                url=r.get("href") or self.login_url, sku=r.get("id", ""), cart_ref=r.get("id", ""),
            ))
        return out
