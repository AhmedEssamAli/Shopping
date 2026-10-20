"""Quick-commerce shops read in your Shopping Chrome: noon Minutes and Amazon Now.

Both answer a cloud server with a bot check, so the readers were tuned from pages saved on
the home PC (2026-10-20):
- noon Minutes draws product boxes ``a[data-testid=catalog-product-box-<sku>]`` with links
  to ``/egypt-en/now-product/<sku>/``; NOON_MINUTES_JS reads them. Verified on its home page.
- Amazon Now (``/tez/browse``) is a Next.js app. Its search page is
  ``/tez/browse/search?qcbrand=...&searchKeyword=...`` (the pattern is in its own page data).
  Its saved page held no products and no /dp/ links, so its tile layout is not known yet.
  TILES_JS reads any tile with a picture and an EGP price, link or not.

The search flow: the shop's search URL, then its own search box. Readers, in order: the
shop's own reader, Amazon result cards, a product link with a price near it, then tiles.
When nothing is found, the page is saved to ~/.shopper/captures/<shop>/failed-search-*.

Start pages from you: noon Minutes https://minutes.noon.com/egypt-en/ and Amazon Now
https://www.amazon.eg/-/en/tez/browse?qcbrand=sAuWWBROaG
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

from ..browser import BrowserSession, dump_page, safe_evaluate
from ..matching import has_word, tokens
from ..models import Offer
from .amazon_eg import EXTRACT_JS as AMAZON_CARDS, parse_price
from .base import Adapter

GENERIC_JS = r"""() => {
  const price = /(?:EGP|E£|جنيه|ج\.م\.?)\s*[\d٠-٩][\d٠-٩,.]*|[\d٠-٩][\d٠-٩,.]*\s*(?:EGP|E£|جنيه|ج\.م)/;
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!/\/p\/|\/dp\/|product\/|\/item/.test(href) || seen.has(a.href)) continue;
    let el = a;
    for (let i = 0; i < 6 && el && !price.test(el.innerText || ''); i++) el = el.parentElement;
    if (!el || !price.test(el.innerText || '')) continue;
    seen.add(a.href);
    const lines = (el.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const name = a.getAttribute('title') || a.getAttribute('aria-label')
      || lines.filter(t => !price.test(t) && t.length > 6).sort((x, y) => y.length - x.length)[0] || '';
    const prices = (el.innerText || '').match(new RegExp(price.source, 'g')) || [];  // now, then was
    const img = el.querySelector('img');
    out.push({href: a.href, name, price: prices[0] || '', was: prices[1] || '', image: img ? (img.currentSrc || img.src || '') : ''});
  }
  return out;
}"""

NOON_MINUTES_JS = r"""() => [...document.querySelectorAll('a[data-testid^="catalog-product-box-"]')].map(a => {
  const t = s => ((a.querySelector(s) || {}).innerText || '').trim();
  const img = a.querySelector('img[src*="/p/"]') || a.querySelector('img[src^="http"]');
  return {href: a.href, sku: a.dataset.testid.replace('catalog-product-box-', ''),
          name: [t('[data-testid=product-title]'), t('[class*=sizeInfo]')].filter(Boolean).join(' '),
          price: t('[data-testid=product-price] strong'), was: t('[class*=oldPrice]'), image: img ? img.src : ''};
})"""

TILES_JS = r"""() => {
  const price = /(?:EGP|E£|جنيه|ج\.م\.?)\s*[\d٠-٩][\d٠-٩,.]*|[\d٠-٩][\d٠-٩,.]*\s*(?:EGP|E£|جنيه|ج\.م)/;
  const found = [];
  for (const img of document.querySelectorAll('img')) {
    let el = img;
    for (let i = 0; i < 5 && el && !price.test(el.innerText || ''); i++) el = el.parentElement;
    if (el && price.test(el.innerText || '') && !found.includes(el)) found.push(el);
  }
  // A box that holds another tile is a grid or a page section, not a tile.
  return found.filter(el => !found.some(o => o !== el && el.contains(o))).map(el => {
    const lines = (el.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const img = el.querySelector('img'), alt = (img && img.alt || '').trim();
    const prices = (el.innerText || '').match(new RegExp(price.source, 'g')) || [];
    const tagged = el.matches('[data-asin]') ? el : el.querySelector('[data-asin]');
    const link = el.closest('a[href]') || el.querySelector('a[href]');
    return {href: link ? link.href : '', asin: tagged ? tagged.getAttribute('data-asin') : '',
            name: alt.length > 3 ? alt : lines.filter(t => !price.test(t) && t.length > 6).sort((x, y) => y.length - x.length)[0] || '',
            price: prices[0] || '', was: prices[1] || '', image: img ? (img.currentSrc || img.src || '') : ''};
  });
}"""

SEARCH_BOXES = ["#twotabsearchtextbox", "input[type=search]", "input[name=q]", "input[name=k]",
                "input[placeholder*='earch' i]", "input[placeholder*='looking for' i]", "input[placeholder*='بحث']"]
LOST = "You've lost connection"  # Amazon Now's offline notice


class QuickShopAdapter(Adapter):
    needs_browser = True

    def __init__(self, name: str, label: str, start_url: str, session: BrowserSession,
                 search_url: str = "", wait_s: float = 10, reader: str = ""):
        self.name, self.label = name, label
        self.login_url = start_url
        self.search_url = search_url  # with {q}; empty means use the page's search box
        self.session = session
        self.wait_s = wait_s
        self.reader = reader  # the shop's own product reader (JS), tried first

    async def _rows(self, page) -> list[dict]:
        for js in filter(None, [self.reader, AMAZON_CARDS, GENERIC_JS, TILES_JS]):
            rows = await safe_evaluate(page, js, [])
            if js is AMAZON_CARDS:
                rows = [dict(r, href=f"https://www.amazon.eg/dp/{r['asin']}") for r in rows if not r.get("sponsored")]
            if rows:
                return rows
        return []

    async def _read(self, page, want: list[str]) -> list[dict]:
        rows: list[dict] = []
        for _ in range(int(self.wait_s * 2)):
            await page.wait_for_timeout(500)
            rows = await self._rows(page)
            if _relevant(rows, want):
                break
        return rows

    async def _offline(self, page) -> bool:
        try:
            return await page.get_by_text(LOST).first.is_visible()
        except Exception:  # noqa: BLE001 - a page that went away is not "offline"
            return False

    async def _search_box(self, page):
        """The first editable search box. A read-only box (Amazon Now) opens the real one on click."""
        for attempt in range(2):
            for sel in SEARCH_BOXES:
                box = page.locator(sel).first
                if not await box.count():
                    continue
                if await box.is_editable():
                    return box
                if attempt == 0:
                    await box.click()
                    await page.wait_for_timeout(1000)
                    break
        return None

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        page = await self.session.new_page()
        want = tokens(query)
        rows: list[dict] = []
        try:
            if self.search_url:
                await page.goto(self.search_url.format(q=quote_plus(query)), wait_until="domcontentloaded", timeout=45000)
                rows = await self._read(page, want)
                if not rows and await self._offline(page):
                    await page.reload(wait_until="domcontentloaded", timeout=45000)
                    rows = await self._read(page, want)
            if not _relevant(rows, want):
                found = rows
                await page.goto(self.login_url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1500)
                box = await self._search_box(page)
                if box:
                    await box.fill(query)
                    await box.press("Enter")
                    rows = await self._read(page, want)
                if not _relevant(rows, want) and found:
                    rows = found
            if not rows:
                note = f" The page says \"{LOST}\": open it in Shopping Chrome and check it loads." if await self._offline(page) else ""
                dump = dump_page(self.session.settings, self.name, query, await page.content(), {"url": page.url})
                raise RuntimeError(f"{self.label}: no products found.{note} Saved the page to {dump} — send me that folder.")
            base = page.url
        finally:
            await page.close()
        return self.parse(rows, base)[:limit]

    def parse(self, rows: list[dict], base: str = "") -> list[Offer]:
        out, seen = [], set()
        for r in rows:
            name = " ".join((r.get("title") or r.get("name") or "").split())
            price = parse_price(r.get("price"))
            href = r.get("href") or (f"https://www.amazon.eg/dp/{r['asin']}" if r.get("asin") else "")
            url = urljoin(base or self.login_url, href)
            if not name or price is None or (url, name) in seen:
                continue
            seen.add((url, name))
            was = parse_price(r.get("regular") or r.get("was"))
            m = re.search(r"/(?:dp/)?([A-Z0-9][A-Z0-9-]{3,})(?:/p)?/?(?:[?#]|$)", url.split("?")[0] + "?")
            sku = r.get("sku") or r.get("asin") or (m.group(1) if m and href else name if not href else url)
            out.append(Offer(self.name, name, price, url, sku=sku, regular_price=was if was and was > price else None,
                             image=r.get("image") if (r.get("image") or "").startswith("http") else ""))
        return out


def _relevant(rows: list[dict], want: list[str]) -> bool:
    return any(any(has_word(w, tokens(r.get("title") or r.get("name") or "")) for w in want) for r in rows)


def noon_minutes(session: BrowserSession) -> QuickShopAdapter:
    return QuickShopAdapter("noon_minutes", "noon Minutes", "https://minutes.noon.com/egypt-en/", session,
                            search_url="https://minutes.noon.com/egypt-en/search/?q={q}", reader=NOON_MINUTES_JS)


def amazon_now(session: BrowserSession) -> QuickShopAdapter:
    return QuickShopAdapter("amazon_now", "Amazon Now",
                            "https://www.amazon.eg/-/en/tez/browse?_encoding=UTF8&qcbrand=sAuWWBROaG", session,
                            search_url="https://www.amazon.eg/tez/browse/search?qcbrand=sAuWWBROaG&searchKeyword={q}")
