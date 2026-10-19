import json
import os
from pathlib import Path

import pytest

from shopper.adapters.amazon_eg import EXTRACT_JS, AmazonEgAdapter, parse_price
from shopper.adapters.magento import gourmet, seoudi, spinneys
from shopper.matching import best_match
from shopper.models import ItemRequest, Offer

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.mark.parametrize("factory, fixture", [
    (gourmet, "magento_gourmet_juhayna_milk.json"),
    (seoudi, "magento_seoudi_juhayna_milk.json"),
    (spinneys, "magento_spinneys_juhayna_milk.json"),
])
def test_magento_parse_real_fixture(factory, fixture):
    offers = factory().parse(load(fixture))
    assert len(offers) == 12
    assert all(o.price > 0 and o.url.startswith("https://") for o in offers)


def test_seoudi_real_fixture_finds_skimmed_1l():
    offers = seoudi().parse(load("magento_seoudi_juhayna_milk.json"))
    m = best_match(ItemRequest("Juhayna skimmed milk 1L"), offers)
    assert m.exact and m.offer.name == "Juhayna Skimmed Milk - 1 Liter" and m.offer.price == 55.45
    assert m.offer.url == "https://seoudisupermarket.com/en/juhayna-skimmed-milk-1-liter"


def test_spinneys_real_fixture_ignores_lactose_and_packs():
    offers = spinneys().parse(load("magento_spinneys_juhayna_milk.json"))
    m = best_match(ItemRequest("Juhayna skimmed milk 1L"), offers)
    assert m.offer.name == "Juhayna - Milk Skimmed - 1l" and m.exact


def test_magento_graphql_error_raises():
    with pytest.raises(RuntimeError):
        gourmet().parse({"errors": [{"message": "boom"}]})


@pytest.mark.parametrize("text, value", [("EGP 1,299.00", 1299.0), ("٦٠٫٠٠ جنيه", 60.0), ("", None), (None, None)])
def test_parse_price(text, value):
    assert parse_price(text) == value


def test_amazon_pushes_instead_of_cart_link():
    # The /gp/aws/cart/add.html link did not add items on Amazon.eg (2026-10-19).
    a = AmazonEgAdapter(session=None)
    assert a.cart_link([(Offer("amazon_eg", "x", 10, "u", cart_ref="B000000001"), 2)]) is None
    assert a.supports_cart_push


# Synthetic page in Amazon's search-result markup (not a live capture).
SYNTHETIC_AMAZON = """
<div data-component-type="s-search-result" data-asin="B0AAA11111">
  <h2 aria-label="Juhayna Full Cream Milk, 1 Liter"><span>Juhayna Full Cream Milk, 1 Liter</span></h2>
  <span class="a-price"><span class="a-offscreen">EGP 62.50</span></span>
  <span class="a-price a-text-price"><span class="a-offscreen">EGP 70.00</span></span>
</div>
<div data-component-type="s-search-result" data-asin="B0BBB22222">
  <h2><span>Sponsored thing</span></h2><span class="puis-sponsored-label-text">Sponsored</span>
  <span class="a-price"><span class="a-offscreen">EGP 10.00</span></span>
</div>
<div data-component-type="s-search-result" data-asin="">
  <h2><span>No asin</span></h2>
</div>
"""


@pytest.mark.skipif(not (os.environ.get("BROWSER_EXECUTABLE") or Path("/opt/pw-browsers/chromium").exists()),
                    reason="needs a Chromium binary")
async def test_amazon_extract_js_on_synthetic_markup():
    from playwright.async_api import async_playwright

    exe = os.environ.get("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=exe)
        page = await b.new_page()
        await page.set_content(SYNTHETIC_AMAZON)
        rows = await page.evaluate(EXTRACT_JS)
        await b.close()
    offers = AmazonEgAdapter(session=None).parse(rows)
    assert len(offers) == 1
    o = offers[0]
    assert (o.name, o.price, o.regular_price, o.url) == (
        "Juhayna Full Cream Milk, 1 Liter", 62.5, 70.0, "https://www.amazon.eg/dp/B0AAA11111")


def test_gourmet_size_comes_from_product_weight():
    offers = gourmet().parse(load("magento_gourmet_juhayna_milk.json"))
    m = best_match(ItemRequest("Juhayna skimmed milk 1L"), offers)
    assert m.exact and m.offer.size.total == 1000 and m.offer.url == "https://gourmetegypt.com/juhayna-skimmed-milk"
