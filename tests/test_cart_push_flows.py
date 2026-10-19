"""Browser flows against synthetic pages served in place of the real sites."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shopper.adapters.amazon_eg import AmazonEgAdapter
from shopper.adapters.instashop import InstashopAdapter
from shopper.models import Offer

CHROMIUM = os.environ.get("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
pytestmark = pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="needs a Chromium binary")


class FakeSession:
    def __init__(self, context, tmp_path):
        self.context = context
        self.settings = SimpleNamespace(captures_dir=tmp_path)

    async def new_page(self):
        return await self.context.new_page()


@pytest.fixture
async def browser():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROMIUM)
        yield b
        await b.close()


async def serve(browser, pages: dict[str, str]):
    ctx = await browser.new_context()

    async def handler(route):
        url = route.request.url
        body = next((html for prefix, html in pages.items() if url.startswith(prefix)), None)
        await (route.fulfill(body=body, content_type="text/html") if body else route.abort())

    await ctx.route("**/*", handler)
    return ctx


AMAZON_DP = """<html><body><span id="nav-cart-count">3</span>
<select id="quantity" name="quantity"><option value="1">1</option><option value="2">2</option><option value="3">3</option></select>
<input id="add-to-cart-button" type="submit" value="Add to Cart" onclick="setTimeout(() => {
  const c = document.querySelector('#nav-cart-count');
  c.textContent = String(+c.textContent + +document.querySelector('#quantity').value); }, 300)">
</body></html>"""


async def test_amazon_add_to_cart_presses_button_and_confirms_count(browser, tmp_path):
    ctx = await serve(browser, {"https://www.amazon.eg/dp/B0OK": AMAZON_DP,
                                "https://www.amazon.eg/dp/B0GONE": "<html><body>Currently unavailable.</body></html>"})
    a = AmazonEgAdapter(FakeSession(ctx, tmp_path))
    a.button_timeout_ms = 1000
    out = await a.add_to_cart([(Offer("amazon_eg", "Nescafe Classic 200g", 261.99, "u", cart_ref="B0OK"), 2),
                               (Offer("amazon_eg", "Gone item", 10, "u", cart_ref="B0GONE"), 1)])
    assert out[0] == "added: 2 × Nescafe Classic 200g"
    assert out[1].startswith("FAILED (no Add to Cart button") and (tmp_path / "amazon_eg").exists()


INSTASHOP_SHOP = """<html><body>
<form class="search-form"><input type="text" formcontrolname="query"></form><div id="slot"></div><div id="tiles"></div>
<script>
const box = document.querySelector('input');
box.addEventListener('input', () => setTimeout(() => {
  document.querySelector('#slot').innerHTML = box.value.length < 3 ? '' :
    '<div class="results"><nb-list-item>Mahmoud Elfar</nb-list-item>'
    + '<nb-list-item onclick="showTiles()">Nescafe Classic Instant Coffee</nb-list-item></div>';
}, 200));
// Enter only closes the list, as on the real site.
document.querySelector('form').addEventListener('submit', e => { e.preventDefault(); document.querySelector('#slot').innerHTML = ''; });
function showTiles() {
  document.querySelector('#slot').innerHTML = '';
  document.querySelector('#tiles').innerHTML = '<app-product><a href="/en-eg/client/x/product/P1">'
    + '<div class="product-title">Nescafe Classic Coffee</div><div class="product-packaging-string">200 gr</div>'
    + '<div class="price-txt">EGP 255.00</div></a></app-product>';
}
</script></body></html>"""


async def test_instashop_search_opens_matching_suggestion(browser, tmp_path):
    ctx = await serve(browser, {"https://instashop.com/en-eg/client/mahmoud-elfar-mivida": INSTASHOP_SHOP})
    a = InstashopAdapter("instashop_x", "ElFar", "mahmoud-elfar-mivida", FakeSession(ctx, tmp_path), wait_s=4)
    offers = await a.search("Nescafe classic")
    assert [(o.name, o.price) for o in offers] == [("Nescafe Classic Coffee - 200 gr", 255.0)]
    assert offers[0].url.endswith("/product/P1")
