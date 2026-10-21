import asyncio
import os
import socket
import subprocess
from pathlib import Path

import pytest

from shopper.browser import BrowserSession
from shopper.config import Settings

CHROMIUM = os.environ.get("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="needs a Chromium binary")
async def test_attach_uses_existing_browser_and_leaves_it_running(tmp_path, monkeypatch):
    port = free_port()
    proc = subprocess.Popen([CHROMIUM, "--headless=new", "--no-sandbox", f"--remote-debugging-port={port}",
                             f"--user-data-dir={tmp_path / 'chrome'}", "--no-first-run", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        monkeypatch.setenv("BROWSER_CDP_URL", f"http://127.0.0.1:{port}")
        for _ in range(150):  # wait for the debug port
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                await asyncio.sleep(0.1)
        s = BrowserSession(Settings(home=tmp_path, platforms={}))
        page = await s.new_page()
        await page.set_content("<p id=x>hi</p>")
        assert await page.inner_text("#x") == "hi"
        await page.close()
        await s.close()
        assert proc.poll() is None  # the user's browser is still running
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_attach_without_chrome_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_CDP_URL", f"http://127.0.0.1:{free_port()}")
    s = BrowserSession(Settings(home=tmp_path, platforms={}))
    with pytest.raises(RuntimeError, match="Shopping Chrome is not running"):
        await s.new_page()


async def test_cdp_info_none_when_nothing_listens():
    from shopper.browser import cdp_info
    assert await cdp_info(f"http://127.0.0.1:{free_port()}") is None


async def test_safe_evaluate_survives_navigation():
    from playwright.async_api import Error as PlaywrightError
    from shopper.browser import safe_evaluate

    class Page:
        calls = 0
        async def evaluate(self, js):
            Page.calls += 1
            raise PlaywrightError("Page.evaluate: Execution context was destroyed, most likely because of a navigation")
        async def wait_for_load_state(self, *a, **k):
            return None

    assert await safe_evaluate(Page(), "() => 1", []) == [] and Page.calls == 1


INSTASHOP_SEARCH_LAYOUT = """<div class="search-results"><div class="result-card">
  <div class="product-title">Nescafe Classic Coffee</div><div class="product-packaging-string">200 gr</div>
  <div class="price-txt"><span>EGP</span><span> 259.95 </span></div><a href="/product/AbC123"></a>
</div></div>"""


@pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="needs a Chromium binary")
async def test_instashop_reads_tiles_outside_app_product():
    from playwright.async_api import async_playwright
    from shopper.adapters.instashop import EXTRACT_JS, InstashopAdapter

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROMIUM)
        page = await b.new_page()
        await page.set_content(INSTASHOP_SEARCH_LAYOUT)
        rows = await page.evaluate(EXTRACT_JS)
        await b.close()
    o = InstashopAdapter("i", "I", "s", session=None).parse(rows)[0]
    assert (o.name, o.price, o.sku) == ("Nescafe Classic Coffee - 200 gr", 259.95, "AbC123")
