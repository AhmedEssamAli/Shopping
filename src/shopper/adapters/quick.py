"""Quick-commerce shops read in your Shopping Chrome: noon Minutes and Amazon Now.

Both answered this machine (a cloud server) with a bot check (2026-10-20), so their pages
are not verified yet. The search flow therefore tries in order: the shop's search URL, then
its own search box, and reads products with Amazon's standard result cards or, failing
that, a generic reader (a product link with an EGP price near it). When nothing is found,
the page is saved to ~/.shopper/captures/<shop>/failed-search-*; send that folder and the
reader gets tuned to the real page.

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
    if (!/\/p\/|\/dp\/|\/product|\/item/.test(href) || seen.has(a.href)) continue;
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

SEARCH_BOXES = ["#twotabsearchtextbox", "input[type=search]", "input[name=q]", "input[name=k]",
                "input[placeholder*='earch' i]", "input[placeholder*='بحث']"]


class QuickShopAdapter(Adapter):
    needs_browser = True

    def __init__(self, name: str, label: str, start_url: str, session: BrowserSession,
                 search_url: str = "", wait_s: float = 10):
        self.name, self.label = name, label
        self.login_url = start_url
        self.search_url = search_url  # with {q}; empty means use the page's search box
        self.session = session
        self.wait_s = wait_s

    async def _read(self, page, want: list[str]) -> list[dict]:
        rows: list[dict] = []
        for _ in range(int(self.wait_s * 2)):
            await page.wait_for_timeout(500)
            rows = [dict(r, href=f"https://www.amazon.eg/dp/{r['asin']}") for r in await safe_evaluate(page, AMAZON_CARDS, [])
                    if not r.get("sponsored")] or await safe_evaluate(page, GENERIC_JS, [])
            if any(any(has_word(w, tokens(r.get("title") or r.get("name") or "")) for w in want) for r in rows):
                break
        return rows

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        page = await self.session.new_page()
        want = tokens(query)
        rows: list[dict] = []
        try:
            if self.search_url:
                await page.goto(self.search_url.format(q=quote_plus(query)), wait_until="domcontentloaded", timeout=45000)
                rows = await self._read(page, want)
            if not rows:
                await page.goto(self.login_url, wait_until="domcontentloaded", timeout=45000)
                for sel in SEARCH_BOXES:
                    box = page.locator(sel).first
                    if await box.count():
                        await box.fill(query)
                        await box.press("Enter")
                        rows = await self._read(page, want)
                        break
            if not rows:
                dump = dump_page(self.session.settings, self.name, query, await page.content(), {"url": page.url})
                raise RuntimeError(f"{self.label}: no products found. Saved the page to {dump} — send me that folder.")
        finally:
            await page.close()
        return self.parse(rows, page.url if rows else self.login_url)[:limit]

    def parse(self, rows: list[dict], base: str = "") -> list[Offer]:
        out, seen = [], set()
        for r in rows:
            name = " ".join((r.get("title") or r.get("name") or "").split())
            price = parse_price(r.get("price"))
            url = urljoin(base or self.login_url, r.get("href") or "")
            if not name or price is None or url in seen:
                continue
            seen.add(url)
            was = parse_price(r.get("regular") or r.get("was"))
            m = re.search(r"/(?:dp/)?([A-Z0-9]{4,})(?:/p)?/?(?:[?#]|$)", url.split("?")[0] + "?")
            sku = r.get("asin") or (m.group(1) if m else url)
            out.append(Offer(self.name, name, price, url, sku=sku, regular_price=was if was and was > price else None,
                             image=r.get("image") if (r.get("image") or "").startswith("http") else ""))
        return out


def noon_minutes(session: BrowserSession) -> QuickShopAdapter:
    return QuickShopAdapter("noon_minutes", "noon Minutes", "https://minutes.noon.com/egypt-en/", session,
                            search_url="https://minutes.noon.com/egypt-en/search/?q={q}")


def amazon_now(session: BrowserSession) -> QuickShopAdapter:
    return QuickShopAdapter("amazon_now", "Amazon Now",
                            "https://www.amazon.eg/-/en/tez/browse?_encoding=UTF8&qcbrand=sAuWWBROaG", session)
