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


async def test_app_shop_prices_from_typed_lines(tmp_path):
    from shopper.adapters.appshop import AppShopAdapter, PriceBook, parse_prices
    from shopper.store import Store
    assert parse_prices("Juhayna skimmed milk 1L = 54.95\nrice 5kg: 210 EGP\nnot a price line\nEggs 30 pcs - 165") == [
        ("Juhayna skimmed milk 1L", 54.95), ("rice 5kg", 210.0), ("Eggs 30 pcs", 165.0)]
    book = PriceBook(Store(tmp_path / "db.sqlite3").db)
    book.add("breadfast", [("Juhayna skimmed milk 1L", 56.0)])
    book.add("breadfast", [("Juhayna Skimmed Milk 1L", 54.95)])  # newer price for the same product wins
    offers = await AppShopAdapter("breadfast", "Breadfast", book).search("juhayna skimmed milk")
    assert [(o.name, o.price) for o in offers] == [("Juhayna Skimmed Milk 1L", 54.95)]
    assert best_match(ItemRequest("Juhayna skimmed milk 1L"), offers).exact


async def test_price_command_and_home_form(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient
    from telegram.ext import CommandHandler

    from shopper.app import App
    from shopper.bot import build_bot
    from shopper.config import PlatformConfig, Settings
    from shopper.models import PlatformFees
    from shopper.web import create_app
    s = Settings(home=tmp_path, platforms={"rabbit": PlatformConfig("rabbit", "Rabbit", "app", True, PlatformFees())})
    s.telegram_token, s.allowed_chat_ids, s.web_token = "123456:TEST", {42}, "k"
    app = App(s)
    cmd = next(h for h in build_bot(app).handlers[0] if isinstance(h, CommandHandler) and "price" in h.commands)
    sent = AsyncMock()
    upd = SimpleNamespace(effective_chat=SimpleNamespace(id=42), callback_query=None,
                          effective_message=SimpleNamespace(text="/price rabbit Nescafe classic 200g = 255", reply_text=sent))
    await cmd.callback(upd, None)
    assert "Saved 1 Rabbit price" in sent.call_args.args[0]
    client = TestClient(create_app(app))
    client.post("/prices/add?t=k", data={"shop": "rabbit", "lines": "rice 5kg = 199"})
    home = client.get("/?t=k").text
    assert "Prices from the apps" in home and "Nescafe classic 200g" in home and "199.00" in home
    result, _, _ = await app.run([ItemRequest("Nescafe classic 200g")])
    assert result.best.assignments[0].offer.price == 255.0


async def _rows(html: str, js: str, url: str = "https://minutes.noon.com/egypt-en/search/?q=x"):
    import os
    import pytest
    chromium = os.environ.get("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
    if not Path(chromium).exists():
        pytest.skip("needs Chromium")
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=chromium)
        page = await b.new_page()
        await page.route("**/*", lambda r: r.fulfill(body=html, content_type="text/html; charset=utf-8"))
        await page.goto(url)
        rows = await page.evaluate(js)
        await b.close()
    return rows


async def test_noon_minutes_reads_its_product_boxes():
    from shopper.adapters.quick import NOON_MINUTES_JS, noon_minutes
    rows = await _rows((FIX / "noon_minutes_boxes.html").read_text(encoding="utf-8"), NOON_MINUTES_JS)
    offers = noon_minutes(None).parse(rows)
    assert len(offers) == 20
    candy = next(o for o in offers if o.sku == "Z056694E31609DDFEBF09Z-1")
    assert candy.name == "Bebeto Sour Worms Jelly Candy 35g"  # brand, name and size
    assert candy.price == 14.25 and candy.regular_price == 16.75
    assert candy.url == "https://minutes.noon.com/egypt-en/now-product/Z056694E31609DDFEBF09Z-1/"
    assert candy.image.startswith("https://f.nooncdn.com/p/pzsku/Z056694E31609DDFEBF09Z/")
    assert best_match(ItemRequest("Bebeto sour worms 35g"), offers).exact


async def test_generic_reader_takes_now_product_links():
    from shopper.adapters.quick import GENERIC_JS, QuickShopAdapter
    rows = await _rows('<div><a href="/egypt-en/now-product/Z1-1/"><img src="https://i/1.jpg">'
                       '<h2>Juhayna Skimmed Milk</h2><span>1L</span><span>EGP</span><strong>52.50</strong></a></div>', GENERIC_JS)
    offers = QuickShopAdapter("x", "X", "https://minutes.noon.com/egypt-en/", None).parse(rows)
    assert [(o.price, o.sku) for o in offers] == [(52.5, "Z1-1")]


async def test_tile_reader_without_product_links():
    """Amazon Now draws tiles with click handlers, not links (assumed: its saved page had no /dp/ links)."""
    from shopper.adapters.quick import TILES_JS, amazon_now
    html = """<div class=grid>
      <div class=tile data-asin="B0ABCDEFGH"><img alt="Juhayna Skimmed Milk 1L" src="https://m.media-amazon.com/images/I/a.jpg">
        <p>Juhayna Skimmed Milk 1L</p><span>EGP 52.50</span><s>EGP 55.00</s><button>Add</button></div>
      <div class=tile><img alt="Al Doha Rice 5kg" src="https://m.media-amazon.com/images/I/b.jpg">
        <p>Al Doha Rice 5kg</p><span>E£210</span><button>Add</button></div>
      <div class=banner><img src="https://m.media-amazon.com/images/I/logo.png"><p>Offers</p></div></div>"""
    rows = await _rows(html, TILES_JS, "https://www.amazon.eg/tez/browse/search?qcbrand=sAuWWBROaG&searchKeyword=milk")
    offers = amazon_now(None).parse(rows, "https://www.amazon.eg/tez/browse/search?qcbrand=sAuWWBROaG&searchKeyword=milk")
    assert [(o.name, o.price) for o in offers] == [("Juhayna Skimmed Milk 1L", 52.5), ("Al Doha Rice 5kg", 210.0)]
    assert offers[0].sku == "B0ABCDEFGH" and offers[0].url == "https://www.amazon.eg/dp/B0ABCDEFGH"
    assert offers[0].regular_price == 55.0
    assert offers[1].url.startswith("https://www.amazon.eg/tez/browse/search?") and offers[1].sku


def test_quick_shop_search_urls_and_boxes():
    from shopper.adapters.quick import SEARCH_BOXES, amazon_now
    a = amazon_now(None)
    assert a.search_url == "https://www.amazon.eg/tez/browse/search?qcbrand=sAuWWBROaG&searchKeyword={q}"
    assert any("looking for" in s for s in SEARCH_BOXES)  # noon Minutes: "What are you looking for?"


async def test_debug_keeps_the_results_page(tmp_path):
    import os
    import types
    import pytest
    chromium = os.environ.get("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
    if not Path(chromium).exists():
        pytest.skip("needs Chromium")
    from playwright.async_api import async_playwright
    from shopper.adapters.quick import noon_minutes
    html = (FIX / "noon_minutes_boxes.html").read_text(encoding="utf-8")
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=chromium)
        ctx = await b.new_context()
        await ctx.route("**/*", lambda r: r.fulfill(body=html, content_type="text/html; charset=utf-8"))
        a = noon_minutes(types.SimpleNamespace(new_page=ctx.new_page, settings=types.SimpleNamespace(captures_dir=tmp_path)))
        a.wait_s, a.keep_page = 1, True
        offers = await a.search("Bebeto sour worms")
        await b.close()
    assert offers and len(list((tmp_path / "noon_minutes").glob("search-*/page.html"))) == 1
