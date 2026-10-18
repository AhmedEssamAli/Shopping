import json
import os
from pathlib import Path

import pytest

from shopper.adapters.amazon_eg import EXTRACT_JS as AMAZON_JS, AmazonEgAdapter
from shopper.adapters.instashop import EXTRACT_JS as INSTA_JS, InstashopAdapter
from shopper.adapters.noon import NoonAdapter, embedded_hits
from shopper.matching import best_match
from shopper.models import ItemRequest

FIX = Path(__file__).parent / "fixtures"
CHROMIUM = os.environ.get("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
needs_chromium = pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="needs a Chromium binary")


async def run_js(html: str, js: str):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROMIUM)
        page = await b.new_page()
        await page.set_content(html)
        rows = await page.evaluate(js)
        await b.close()
    return rows


@needs_chromium
async def test_instashop_reads_real_captured_tiles():
    rows = await run_js((FIX / "instashop_products.html").read_text(encoding="utf-8"), INSTA_JS)
    offers = InstashopAdapter("instashop_x", "ElFar", "mahmoud-elfar-mivida", session=None).parse(rows)
    names = [o.name for o in offers]
    assert "Italiano Premium Penne Pasta - 400 gr" in names
    pasta = next(o for o in offers if o.name.startswith("Italiano"))
    assert pasta.price == 25.95 and pasta.sku and "/product/" in pasta.url
    m = best_match(ItemRequest("Italiano penne pasta 400g"), offers)
    assert m.exact and m.offer is pasta


AMAZON_TWO_H2 = """
<div data-component-type="s-search-result" data-asin="B0CCC33333">
  <div data-cy="title-recipe">
    <h2 class="a-size-mini"><span>Nescafe</span></h2>
    <a href="/dp/B0CCC33333"><h2 aria-label="Classic Instant Coffee Jar, 200g"><span>Classic Instant Coffee Jar, 200g</span></h2></a>
  </div>
  <span class="a-price"><span class="a-offscreen">EGP 261.99</span></span>
</div>"""


@needs_chromium
async def test_amazon_brand_line_plus_title():
    offers = AmazonEgAdapter(session=None).parse(await run_js(AMAZON_TWO_H2, AMAZON_JS))
    assert offers[0].name == "Nescafe Classic Instant Coffee Jar, 200g"
    assert best_match(ItemRequest("Nescafe classic 200g"), offers).exact


def test_noon_embedded_flight_data():
    hit = {"offer_code": "abc123", "sku": "N1", "name": "Nescafe Classic 200g", "price": 300, "sale_price": 270,
           "pdp_url": "/nescafe-classic-200g/N1/p/?", "is_buyable": True}
    # Next.js flight data puts JSON inside a JS string, so quotes arrive escaped.
    flight = 'self.__next_f.push([1,"' + json.dumps({"hits": [hit]}).replace('"', '\\"') + '"])'
    html = f"<html><script>{flight}</script></html>"
    got = embedded_hits(html)
    assert got and got[0]["offer_code"] == "abc123"
    o = NoonAdapter(session=None).parse(got)[0]
    assert o.price == 270 and o.regular_price == 300


def test_instashop_shops_expand_to_platforms():
    from shopper.config import REPO_ROOT, load_platforms
    plats = load_platforms(REPO_ROOT / "config" / "platforms.toml")
    p = plats["instashop_mahmoud_elfar_mivida"]
    assert p.status == "browser" and p.options["slug"] == "mahmoud-elfar-mivida" and "instashop" not in plats
