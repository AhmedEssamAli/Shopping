"""The signed-in browser on the home PC: you sign in to each platform once and every
later search reuses the cookies. No passwords are stored by this tool.

Two modes:
- BROWSER_CDP_URL set (recommended): shopper attaches to a "Shopping Chrome" you start
  yourself (scripts/windows/start-shopping-chrome.cmd). It is ordinary Chrome, not an
  automation-launched one, so sites see a normal browser; shopper only opens and closes
  background tabs in it and never closes your window.
- Otherwise: shopper launches its own Chrome on ``~/.shopper/profile``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .config import Settings


class BrowserSession:
    def __init__(self, settings: Settings, headless: bool | None = None):
        self.settings = settings
        self.headless = settings.headless if headless is None else headless
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self.cdp_url = os.environ.get("BROWSER_CDP_URL", "").strip()

    async def start(self) -> BrowserContext:
        async with self._lock:  # concurrent searches must not launch two browsers on one profile
            if not self.context:
                await self._launch()
            return self.context

    async def _attach(self) -> None:
        timeout_ms = int(float(os.environ.get("BROWSER_CDP_TIMEOUT_S", "60")) * 1000)
        try:
            browser = await self._pw.chromium.connect_over_cdp(self.cdp_url, timeout=timeout_ms)
        except Exception as e:
            await self._pw.stop()
            self._pw = None
            info = await cdp_info(self.cdp_url)
            if info is None:
                raise RuntimeError(f"Shopping Chrome is not running at {self.cdp_url}. Start it with "
                                   "scripts\\windows\\start-shopping-chrome.cmd, then retry.") from e
            raise RuntimeError(
                f"Shopping Chrome ({info['browser']}, {info['tabs']} open tabs) is running, but attaching took longer "
                f"than {timeout_ms // 1000}s. Close extra tabs, run `python -m pip install -U playwright`, "
                "or raise BROWSER_CDP_TIMEOUT_S in .env. `shopper browser-check` times the attach.") from e
        self.context = browser.contexts[0] if browser.contexts else await browser.new_context()

    async def _launch(self) -> None:
        self._pw = await async_playwright().start()
        if self.cdp_url:
            return await self._attach()
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        opts = dict(
            user_data_dir=str(self.settings.profile_dir),
            headless=self.headless,
            locale="en-US",
            timezone_id="Africa/Cairo",
            geolocation={"latitude": self.settings.area_lat, "longitude": self.settings.area_lon},
            permissions=["geolocation"],
            viewport={"width": 1366, "height": 900},
        )
        exe = os.environ.get("BROWSER_EXECUTABLE")
        if exe:
            opts["executable_path"] = exe
        else:
            # Real Chrome looks like a normal user to bot checks; fall back to bundled Chromium.
            opts["channel"] = os.environ.get("BROWSER_CHANNEL", "chrome")
        try:
            try:
                self.context = await self._pw.chromium.launch_persistent_context(**opts)
            except Exception:
                if "channel" not in opts:
                    raise
                opts.pop("channel")
                self.context = await self._pw.chromium.launch_persistent_context(**opts)
        except Exception:
            await self._pw.stop()
            self._pw = None
            raise

    async def new_page(self) -> Page:
        ctx = await self.start()
        return await ctx.new_page()

    async def cookie_count(self, url: str) -> int:
        ctx = await self.start()
        return len(await ctx.cookies([url]))

    async def close(self) -> None:
        if self.context and not self.cdp_url:  # never close the user's own Chrome window
            await self.context.close()
        self.context = None
        if self._pw:
            await self._pw.stop()
            self._pw = None


async def _wait_enter(prompt: str) -> None:
    await asyncio.to_thread(input, prompt)


async def login(settings: Settings, name: str, url: str) -> None:
    """Open the platform in the profile, visible, and wait while you sign in."""
    session = BrowserSession(settings, headless=False)
    page = await session.new_page()
    await page.goto(url)
    await _wait_enter(
        f"\nSign in to {name} in the opened window (OTP if asked) and set your delivery address "
        f"to {settings.area_name}. Then press Enter here... "
    )
    n = await session.cookie_count(url)
    await session.close()
    print(f"{name}: profile now holds {n} cookies for {url}")


async def capture(settings: Settings, name: str, url: str) -> Path:
    """Record what a platform's own search sends back, so an adapter can be calibrated.

    You search by hand in the opened window; every JSON response and the final HTML are
    saved under ~/.shopper/captures/<platform>/<timestamp>/. Nothing is sent anywhere.
    """
    out = settings.captures_dir / name / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    session = BrowserSession(settings, headless=False)
    page = await session.new_page()
    index: list[dict] = []

    async def on_response(resp):
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            return
        try:
            body = await resp.text()
        except Exception:
            return
        fname = f"{len(index):03d}_{re.sub(r'[^a-zA-Z0-9]+', '_', resp.url.split('?')[0])[-80:]}.json"
        (out / fname).write_text(body, encoding="utf-8")
        index.append({"file": fname, "url": resp.url, "status": resp.status, "method": resp.request.method,
                      "post_data": resp.request.post_data})

    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
    await page.goto(url)
    await _wait_enter(f"\nSearch for one item on {name} in the opened window, open a product, add it to the cart. "
                      "Then press Enter here... ")
    (out / "page.html").write_text(await page.content(), encoding="utf-8")
    (out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    await session.close()
    print(f"Saved {len(index)} JSON responses and page.html to {out}")
    return out


async def cdp_info(cdp_url: str) -> dict | None:
    """Browser version and open-tab count from Chrome's debug endpoint, or None if unreachable."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as http:
            version = (await http.get(f"{cdp_url.rstrip('/')}/json/version")).json()
            targets = (await http.get(f"{cdp_url.rstrip('/')}/json/list")).json()
    except Exception:
        return None
    return {"browser": version.get("Browser", "?"), "tabs": sum(t.get("type") == "page" for t in targets)}
