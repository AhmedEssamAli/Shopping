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
