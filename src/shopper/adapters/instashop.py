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

Fixed 2026-10-21: when the popup did not open, the reader fell back to the shop page's
featured tiles, so a search "worked" but returned unrelated products and nothing matched,
with no error. Now only popup rows count; no popup is an error with the page saved.

More shops: Instashop lists many shops (supermarkets, pharmacies, butchers) like talabat.
``shopper instashop-shops`` reads the shop links from the page in your Shopping Chrome and
prints lines for config/platforms.toml.
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
    const img = el.querySelector('img');
    return {title: txt(el, 'h5'), size: txt(el, '.packagingString'), price: txt(el, '.price'), was: txt(el, was),
            href: link ? link.href : '', id: link ? link.getAttribute('href').split('/product/')[1] : '',
            image: img ? (img.currentSrc || img.src || '') : ''};
  });
  // Product tiles on shop and category pages: app-product, or the nearest ancestor of a
  // title that also holds a price.
  const tiles = [...new Set([...document.querySelectorAll('.product-title')].map(t =>
    t.closest('app-product') || (() => { let e = t; for (let i = 0; i < 6 && e; i++) {
      e = e.parentElement; if (e && e.querySelector('.price-txt')) return e; } return null; })()
  ).filter(Boolean))].map(el => {
    const link = el.querySelector('a[href*="/product/"]');
    const img = el.querySelector('img');
    return {title: txt(el, '.product-title'), size: txt(el, '.product-packaging-string'), price: txt(el, '.price-txt'),
            was: txt(el, was), href: link ? link.href : '', id: link ? link.getAttribute('href').split('/product/')[1] : '',
            image: img ? (img.currentSrc || img.src || '') : ''};
  });
  return [...popup, ...tiles].filter(x => x.title && x.price);
}"""

# The popup part of EXTRACT_JS alone: search results, without the shop page's featured tiles.
POPUP_JS = EXTRACT_JS.replace("return [...popup, ...tiles].filter(x => x.title && x.price);",
                              "return popup.filter(x => x.title && x.price);")
assert POPUP_JS != EXTRACT_JS

SHOPS_JS = """() => {
  const out = {};
  for (const a of document.querySelectorAll('a[href*="/client/"]')) {
    const m = a.getAttribute('href').match(/\\/client\\/([\\w-]+)/);
    if (!m) continue;
    const name = (a.getAttribute('title') || a.getAttribute('aria-label') || a.innerText || '').trim().split('\\n')[0];
    if (!out[m[1]] || (name && out[m[1]].length < name.length)) out[m[1]] = name;
  }
  return Object.entries(out).map(([slug, name]) => ({slug, name}));
}"""


async def list_shops(session: BrowserSession, url: str = "https://instashop.com/en-eg", scrolls: int = 8) -> list[dict]:
    """Shops Instashop shows for the address set in your Shopping Chrome, read from its rendered page."""
    page = await session.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        for _ in range(scrolls):  # the list loads as you scroll
            await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(800)
        shops = await safe_evaluate(page, SHOPS_JS, [])
        if not shops:
            dump = dump_page(session.settings, "instashop", "shop list", await page.content(), {"url": page.url})
            raise RuntimeError(f"No Instashop shop links found on {page.url}. Open Instashop in Shopping Chrome, set "
                               f"your address, and try again. Saved the page to {dump}.")
        return shops
    finally:
        await page.close()


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
        self.keep_page = False  # --debug: save the results page too

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
            rows, popup = await self._poll(page, want)
            if self.keep_page or not popup:
                dump = dump_page(self.session.settings, self.name, query, await page.content(), {"url": page.url},
                                 kind="search" if popup else "failed-search")
                if not popup:
                    raise RuntimeError(f"{self.label}: the search results did not open (the page may ask for your "
                                       f"address or a sign-in). Saved the page to {dump} — send me that folder.")
        finally:
            await page.close()
        return self.parse(rows)[:limit]

    @staticmethod
    def _relevant(rows: list[dict], want: list[str]) -> bool:
        return any(any(has_word(w, tokens(r["title"])) for w in want) for r in rows)

    async def _poll(self, page, want: list[str]) -> tuple[list[dict], bool]:
        """Wait for the results popup to show this query. Returns (popup rows, whether the popup opened).
        Only popup rows count: the shop page's own tiles are not search results."""
        rows: list[dict] = []
        popup = False
        for _ in range(int(self.wait_s * 2)):
            await page.wait_for_timeout(500)
            popup = await safe_evaluate(page, "() => !!document.querySelector('app-search-page')", False)
            rows = [r for r in await safe_evaluate(page, POPUP_JS, []) if r.get("title")]
            if popup and self._relevant(rows, want):
                await page.wait_for_timeout(1000)
                return [r for r in await safe_evaluate(page, POPUP_JS, rows) if r.get("title")], True
        return rows, popup

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
                image=r.get("image") if (r.get("image") or "").startswith("http") else "",
            ))
        return out
