"""Instashop shops (e.g. Mahmoud ElFar) read from the rendered page in your Shopping Chrome.

Why the page and not the API: Instashop's product API answers with AES-encrypted bodies
and needs a reCAPTCHA token per request. shopper does not break either. It reads the
same tiles you see on screen.

Verified from a home-PC capture (2026-10-18): each product is an ``app-product`` with
``.product-title``, ``.product-packaging-string`` (size), ``.price-txt`` (EGP price) and a
``/product/<id>`` link.

Search (from a failed-search dump, 2026-10-19): the header box
``input[formcontrolname=query]`` ("Search for shops, categories or products") is a
type-ahead. It lists suggestions in ``.results nb-list-item`` while you type; pressing Enter
only closes that list and leaves the shop page as it was. So shopper types the query, waits
for the suggestions, and opens the one that best matches. Not yet verified: what a
suggestion opens (a product list or one product).
"""
from __future__ import annotations

import re

from ..browser import BrowserSession, dump_page, safe_evaluate
from ..matching import has_word, tokens
from ..models import Offer
from .base import Adapter

BASE = "https://instashop.com/en-eg/client"

EXTRACT_JS = """() => [...new Set([...document.querySelectorAll('.product-title')].map(t =>
    // The tile is app-product on shop pages; search results may wrap tiles differently,
    // so climb from each title to the nearest ancestor that also holds a price.
    t.closest('app-product') || (() => { let e = t; for (let i = 0; i < 6 && e; i++) {
      e = e.parentElement; if (e && e.querySelector('.price-txt')) return e; } return null; })()
  ).filter(Boolean))].map(el => {
    const t = s => (el.querySelector(s)?.textContent || '').trim();
    const link = el.querySelector('a[href*="/product/"]');
    return {title: t('.product-title'), size: t('.product-packaging-string'),
            price: t('.price-txt'), was: t('.old-price, .price-before, .strike-price, s, del'),
            href: link ? link.href : '', id: link ? link.getAttribute('href').split('/product/')[1] : ''};
  }).filter(x => x.title && x.price)"""

SUGGESTION_SELECTOR = ".results nb-list-item, .results a, .results li"
SUGGESTIONS_JS = """() => [...document.querySelectorAll('""" + SUGGESTION_SELECTOR + """')]
  .map((el, i) => ({i, text: (el.innerText || '').replace(/\\s+/g, ' ').trim(),
                    href: (el.closest('a') || el.querySelector('a'))?.href || ''}))
  .filter(x => x.text)"""

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
            await box.press_sequentially(query, delay=60)  # key by key, so the type-ahead fires
            rows, suggestions = await self._poll(page, want, suggestions=True)
            listing = await page.content() if suggestions else ""
            if not self._relevant(rows, want) and suggestions:
                pick = max(suggestions, key=lambda x: sum(has_word(w, tokens(x["text"])) for w in want))
                await page.locator(SUGGESTION_SELECTOR).nth(pick["i"]).click()
                rows, _ = await self._poll(page, want)
            if not self._relevant(rows, want) and not suggestions:
                await box.press("Enter")
                rows, _ = await self._poll(page, want)
            if not rows:
                extra = {"suggestions.html": listing} if listing else None
                dump = dump_page(self.session.settings, self.name, query, await page.content(),
                                 {"url": page.url, "suggestions": suggestions}, extra)
                raise RuntimeError(f"{self.label}: no products after searching. Saved the page to {dump} — send me that folder.")
        finally:
            await page.close()
        return self.parse(rows)[:limit]

    @staticmethod
    def _relevant(rows: list[dict], want: list[str]) -> bool:
        return any(any(has_word(w, tokens(r["title"])) for w in want) for r in rows)

    async def _poll(self, page, want: list[str], suggestions: bool = False) -> tuple[list[dict], list[dict]]:
        """Wait until product tiles reflect the query, or (if asked) the type-ahead lists suggestions."""
        rows: list[dict] = []
        sugg: list[dict] = []
        for _ in range(int(self.wait_s * 2)):
            await page.wait_for_timeout(500)
            rows = await safe_evaluate(page, EXTRACT_JS, [])
            if self._relevant(rows, want):
                break
            if suggestions:
                sugg = await safe_evaluate(page, SUGGESTIONS_JS, [])
                if sugg:
                    await page.wait_for_timeout(1000)  # let the list finish filling
                    sugg = await safe_evaluate(page, SUGGESTIONS_JS, sugg)
                    break
        return rows, sugg

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
