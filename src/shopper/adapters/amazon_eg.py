"""Amazon.eg through the signed-in browser profile.

Status: NOT verified against live Amazon.eg (Amazon blocks cloud IPs). The selectors are
Amazon's long-standing search-result markup; confirm with `shopper search` on the home PC.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlencode

from ..browser import BrowserSession
from ..models import Offer
from .base import Adapter

BASE = "https://www.amazon.eg"

EXTRACT_JS = """() => [...document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')]
  .map(el => {
    const h2 = el.querySelector('h2');
    const title = h2 ? (h2.getAttribute('aria-label') || h2.innerText || '') : '';
    const now = el.querySelector('.a-price:not(.a-text-price) .a-offscreen');
    const was = el.querySelector('.a-price.a-text-price .a-offscreen');
    const sponsored = !!el.querySelector('.puis-sponsored-label-text, .s-sponsored-label-info-icon');
    return {asin: el.getAttribute('data-asin'), title: title.trim(),
            price: now ? now.textContent : null, regular: was ? was.textContent : null, sponsored};
  })
  .filter(x => x.asin && x.title)"""

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def parse_price(text: str | None) -> float | None:
    if not text:
        return None
    t = text.translate(_ARABIC_DIGITS).replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", t)
    return float(m.group()) if m else None


class AmazonEgAdapter(Adapter):
    name = "amazon_eg"
    label = "Amazon.eg"
    needs_browser = True
    login_url = BASE

    def __init__(self, session: BrowserSession):
        self.session = session

    def search_url(self, query: str) -> str:
        return f"{BASE}/s?k={quote_plus(query)}&language=en_AE"

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        page = await self.session.new_page()
        try:
            await page.goto(self.search_url(query), wait_until="domcontentloaded", timeout=45000)
            if await page.locator("form[action*='validateCaptcha']").count():
                raise RuntimeError("Amazon showed a captcha. Run with BROWSER_HEADLESS=0, solve it once, retry.")
            await page.wait_for_selector('div[data-component-type="s-search-result"]', timeout=15000)
            rows = await page.evaluate(EXTRACT_JS)
        finally:
            await page.close()
        return self.parse(rows)[:limit]

    def parse(self, rows: list[dict]) -> list[Offer]:
        offers = []
        for r in rows:
            price = parse_price(r.get("price"))
            if price is None or r.get("sponsored"):
                continue
            offers.append(
                Offer(
                    platform=self.name,
                    name=" ".join(r["title"].split()),
                    price=price,
                    regular_price=parse_price(r.get("regular")),
                    url=f"{BASE}/dp/{r['asin']}",
                    sku=r["asin"],
                    cart_ref=r["asin"],
                )
            )
        return offers

    def cart_link(self, lines: list[tuple[Offer, int]]) -> str | None:
        # Amazon's documented Add-to-Cart form; .eg support not yet confirmed live.
        params = {}
        for i, (o, qty) in enumerate(lines, start=1):
            params[f"ASIN.{i}"] = o.cart_ref
            params[f"Quantity.{i}"] = qty
        return f"{BASE}/gp/aws/cart/add.html?{urlencode(params)}" if params else None
