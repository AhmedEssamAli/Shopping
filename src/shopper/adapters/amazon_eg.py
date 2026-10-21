"""Amazon.eg through the signed-in Shopping Chrome.

Search is verified live on the home PC (2026-10-18). Cart push opens each product page and
presses Add to Cart, the same as you would. Its selectors (``#add-to-cart-button``,
``select#quantity``, ``#nav-cart-count``) are Amazon's long-standing product-page markup,
not yet verified on Amazon.eg; a failed push saves the page for diagnosis.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..browser import BrowserSession, dump_page, safe_evaluate
from ..models import Offer
from .base import Adapter

BASE = "https://www.amazon.eg"

EXTRACT_JS = """() => [...document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')]
  .map(el => {
    // Newer cards have two h2: the brand line, then the full title. Take the longest one,
    // and prefix the brand when the title does not already start with it.
    const heads = [...el.querySelectorAll('h2')].map(h => (h.getAttribute('aria-label') || h.innerText || '').trim()).filter(Boolean);
    let title = heads.reduce((a, b) => b.length > a.length ? b : a, '');
    const brand = heads.length > 1 ? heads[0] : '';
    if (brand && brand !== title && !title.toLowerCase().startsWith(brand.toLowerCase())) title = brand + ' ' + title;
    const now = el.querySelector('.a-price:not(.a-text-price) .a-offscreen');
    const was = el.querySelector('.a-price.a-text-price .a-offscreen');
    const sponsored = !!el.querySelector('.puis-sponsored-label-text, .s-sponsored-label-info-icon');
    const img = el.querySelector('img.s-image');
    return {asin: el.getAttribute('data-asin'), title: title.trim(), image: img ? (img.currentSrc || img.src || '') : '',
            price: now ? now.textContent : null, regular: was ? was.textContent : null, sponsored};
  })
  .filter(x => x.asin && x.title)"""

CART_COUNT_JS = """() => { const e = document.querySelector('#nav-cart-count');
  const n = e ? parseInt((e.textContent || '').replace(/[^0-9]/g, ''), 10) : NaN; return isNaN(n) ? null : n; }"""
ADDED_JS = """() => !!document.querySelector('#NATC_SMART_WAGON_CONF_MSG_SUCCESS, #sw-atc-confirmation, '
  + '#attach-added-to-cart-message, #huc-v2-order-row-confirm-text, #sw-atc-details-single-container')"""

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
                    image=r.get("image") or "",
                )
            )
        return offers

    # No cart_link: Amazon's /gp/aws/cart/add.html form did not add items on Amazon.eg
    # (tested 2026-10-19). Cart push below replaces it.
    supports_cart_push = True
    button_timeout_ms = 15000

    async def add_to_cart(self, lines: list[tuple[Offer, int]]) -> list[str]:
        page = await self.session.new_page()
        out = []
        try:
            for offer, qty in lines:
                out.append(await self._add_one(page, offer, qty))
        finally:
            await page.close()
        return out

    async def _add_one(self, page, offer: Offer, qty: int, wait_s: float = 15) -> str:
        what = f"{qty} × {offer.name}"
        await page.goto(f"{BASE}/dp/{offer.cart_ref}", wait_until="domcontentloaded", timeout=45000)
        if await page.locator("form[action*='validateCaptcha']").count():
            return f"FAILED (captcha; solve it in Shopping Chrome and retry): {what}"
        button = page.locator("#add-to-cart-button").first
        try:
            await button.wait_for(state="visible", timeout=self.button_timeout_ms)
        except Exception:
            dump = dump_page(self.session.settings, self.name, offer.cart_ref, await page.content(), {"url": page.url})
            return f"FAILED (no Add to Cart button: out of stock or only other sellers; page saved to {dump}): {what}"
        before = await safe_evaluate(page, CART_COUNT_JS, None)
        qty_set = qty == 1
        if qty > 1 and await page.locator("select#quantity").count():
            try:
                await page.locator("select#quantity").first.select_option(str(qty), timeout=5000)
                qty_set = True
            except Exception:
                pass
        await button.click()
        after = before
        for _ in range(int(wait_s * 2)):
            await page.wait_for_timeout(500)
            after = await safe_evaluate(page, CART_COUNT_JS, after)
            if (before is not None and after is not None and after > before) or await safe_evaluate(page, ADDED_JS, False):
                break
        else:
            dump = dump_page(self.session.settings, self.name, offer.cart_ref, await page.content(), {"url": page.url})
            return f"NOT CONFIRMED (cart count stayed {before}; page saved to {dump}): {what}"
        note = "" if qty_set else f" (added 1; set the quantity to {qty} in the cart)"
        return f"added{note}: {what}"
