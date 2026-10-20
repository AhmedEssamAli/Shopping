import json
from pathlib import Path

from shopper.adapters.magento import hyperone
from shopper.adapters.metro import MetroAdapter
from shopper.matching import best_match
from shopper.models import ItemRequest

FIX = Path(__file__).parent / "fixtures"


def test_metro_reads_real_search_cards():
    offers = MetroAdapter().parse((FIX / "metro_search_nescafe.html").read_text(encoding="utf-8"))
    assert len(offers) == 12
    gold = next(o for o in offers if o.name == "Nescafe Gold Instant Coffee - 190g")
    assert gold.price == 475.0 and gold.regular_price == 509.95 and gold.on_deal
    assert gold.url == "https://www.metro-markets.com/product/Nescafe-Gold-Instant-Coffee---190g/5485" and gold.sku == "5485"
    m = best_match(ItemRequest("Nescafe classic 47.5g"), offers)
    assert m.exact and m.offer.price == 89.95


def test_hyperone_uses_magento_and_product_urls():
    offers = hyperone().parse(json.loads((FIX / "hyperone_juhayna_milk.json").read_text()))
    milk = next(o for o in offers if o.name == "Juhayna Full Cream Milk - 1L")
    assert milk.price == 50.95 and milk.url == "https://www.hyperone.com.eg/en/product/1-6223000350065"


def test_new_shops_configured():
    from shopper.config import REPO_ROOT, load_platforms
    plats = load_platforms(REPO_ROOT / "config" / "platforms.toml")
    assert plats["metro_markets"].enabled and plats["metro_markets"].status == "live"
    assert not plats["hyperone"].enabled  # no New Cairo branch found yet


def test_metro_card_without_price_is_skipped_not_shifted():
    page = (FIX / "metro_search_nescafe.html").read_text(encoding="utf-8")
    first = page.index('<p class="after">')
    broken = page[:first] + page[page.index("</p>", first) + 4:]
    offers = MetroAdapter().parse(broken)
    assert len(offers) == 11 and all(o.sku != "5485" for o in offers)
    assert next(o for o in offers if o.sku == "5486").price == 279.99


def test_incidental_words_do_not_split_one_product():
    from shopper.memory import product_cards
    from shopper.models import Offer
    offers = [Offer("a", "Nescafe Classic Instant Coffee 200g", 270, "u1"),
              Offer("b", "Nescafe Classic Pure Soluble Coffee 200g", 275, "u2")]
    assert len(product_cards(ItemRequest("Nescafe classic 200g"), offers)) == 1


def test_photos_from_metro_and_on_the_page(tmp_path):
    offers = MetroAdapter().parse((FIX / "metro_search_nescafe.html").read_text(encoding="utf-8"))
    assert offers[0].image.startswith("https://metro-website-images.s3.eu-west-1.amazonaws.com/")
    from shopper.report import _img
    from shopper.models import Offer
    assert "referrerpolicy=no-referrer" in _img(offers[0]) and _img(Offer("x", "n", 1, "u")) == ""
    assert "&lt;" in _img(Offer("x", "n", 1, "u", image="https://a/<b>"))


async def test_quick_shop_generic_reader_on_a_product_grid():
    import os
    import pytest
    from shopper.adapters.quick import GENERIC_JS, QuickShopAdapter
    chromium = os.environ.get("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
    if not Path(chromium).exists():
        pytest.skip("needs Chromium")
    from playwright.async_api import async_playwright
    grid = """<div class=grid>
      <div class=tile><a href="/egypt-en/juhayna-skimmed-milk-1l/N123/p/"><img src="https://img/x.jpg">
        <div>Juhayna Skimmed Milk 1L</div></a><span>EGP 52.50</span><s>EGP 55.00</s></div>
      <div class=tile><a href="/egypt-en/rice-5kg/N456/p/"><div>Al Doha Rice 5kg</div></a><span>210 EGP</span></div>
      <a href="/about">About us</a></div>"""
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=chromium)
        page = await b.new_page()
        await page.set_content(grid)
        rows = await page.evaluate(GENERIC_JS)
        await b.close()
    offers = QuickShopAdapter("noon_minutes", "noon Minutes", "https://minutes.noon.com/egypt-en/", None).parse(rows)
    assert [(o.name, o.price) for o in offers] == [("Juhayna Skimmed Milk 1L", 52.5), ("Al Doha Rice 5kg", 210.0)]
    assert offers[0].regular_price == 55.0 and offers[0].image == "https://img/x.jpg" and offers[0].sku == "N123"
    best = best_match(ItemRequest("Juhayna skimmed milk 1L"), offers)
    assert best.exact
