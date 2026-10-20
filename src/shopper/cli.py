"""shopper — command line.

  shopper platforms                     status of every platform
  shopper login <platform|all>          sign in once in the persistent browser profile
  shopper capture <platform>            record a platform's search traffic for calibration
  shopper search "<item>"               compare one item
  shopper basket "<list>" | -f FILE     compare a basket (one item per line)
  shopper health                        check sessions and live adapters
  shopper browser-check                 check that shopper can attach to Shopping Chrome, and how fast
  shopper watch-check                   re-price all watches now and print alerts
  shopper talabat-shops                 list every talabat grocery shop that delivers to you
  shopper refresh talabat               re-read all talabat shop catalogues now (or one: talabat_<id>)
  shopper serve                         run Telegram bot + web page (home PC)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .app import App
from .browser import capture, login
from .config import load_settings
from .models import ItemRequest
from .parsing import parse_list
from .report import debug_text, to_html


def _cmd_platforms(args) -> None:
    s = load_settings()
    for p in s.platforms.values():
        fee = "fees verified" if p.fees.verified else "fees not verified"
        print(f"{p.name:10} {p.status:8} {'on ' if p.enabled else 'off'}  {p.label}  ({fee})")


async def _cmd_login(args) -> None:
    app = App()
    # Only the browser platforms have a login; the catalogues and talabat need none.
    names = [n for n, a in app.adapters.items() if a.needs_browser] if args.platform == "all" else [args.platform]
    for n in names:
        a = app.adapters.get(n)
        if not a:
            sys.exit(f"Unknown platform: {n}")
        await login(app.settings, a.label, a.login_url)
    await app.close()


async def _cmd_capture(args) -> None:
    app = App()
    a = app.adapters.get(args.platform)
    if not a:
        sys.exit(f"Unknown platform: {args.platform}. Choose one of: {', '.join(sorted(app.adapters))}")
    await capture(app.settings, args.platform, a.login_url)
    await app.close()


async def _compare(requests: list[ItemRequest], args) -> None:
    app = App()
    if args.platforms:
        only = set(args.platforms.split(","))
        for p in app.settings.platforms.values():
            p.enabled = p.name in only
    if args.debug:
        for a in app.adapters.values():
            if hasattr(a, "keep_page"):
                a.keep_page = True
    try:
        result, text, rid = await app.run(requests, max_orders=args.max_orders, exact_only=args.exact)
        print(text)
        if args.debug:
            print(debug_text(result, app.settings))
        if args.push:
            print("\nCart push:")
            print("\n".join(await app.push_carts(result)))
        if args.html:
            Path(args.html).write_text(to_html(result, app.settings, app.adapters), encoding="utf-8")
            print(f"\nHTML saved to {args.html}")
    finally:
        await app.close()


async def _cmd_search(args) -> None:
    # The same parser as the bot and the web page: "2 milk 1L" is 2 × milk, Arabic is translated.
    from .parsing import parse_list

    requests = parse_list(args.item, load_settings().anthropic_key)[:1] or [ItemRequest(args.item, 1)]
    await _compare(requests, args)


async def _cmd_basket(args) -> None:
    text = Path(args.file).read_text(encoding="utf-8") if args.file else (args.items or "")
    requests = parse_list(text, load_settings().anthropic_key)
    if not requests:
        sys.exit("Empty list.")
    await _compare(requests, args)


async def _cmd_health(args) -> None:
    app = App()
    try:
        for p in app.settings.platforms.values():
            a = app.adapters.get(p.name)
            if not p.enabled or a is None:
                print(f"{p.name:10} off")
                continue
            if p.status == "live":
                try:
                    n = len(await a.search("milk", 3))
                    print(f"{p.name:10} live     search ok ({n} results)")
                except Exception as e:
                    print(f"{p.name:10} live     FAILED {type(e).__name__}: {e}")
            elif app.browser:
                n = await app.browser.cookie_count(a.login_url)
                hint = "" if n else "  -> run: shopper login " + p.name
                print(f"{p.name:10} browser  {n} cookies in profile{hint}")
    finally:
        await app.close()


async def _cmd_watch_check(args) -> None:
    from .watcher import check_watches

    app = App()
    try:
        alerts = await check_watches(app)
        for w in app.store.list_watches():
            print(f"#{w.id} chat {w.chat_id}: {w.query} -> {w.last_price} ({w.last_platform})")
        print("\n".join(a.text for a in alerts) or "No alerts.")
        if alerts and app.settings.telegram_token:
            # This check saved the new prices, so the bot will not alert again: send them now.
            from telegram import Bot

            from .bot import alert_keyboard

            async with Bot(app.settings.telegram_token) as bot:
                for a in alerts:
                    await bot.send_message(a.chat_id, a.text, disable_web_page_preview=True, reply_markup=alert_keyboard(a))
            print(f"Sent {len(alerts)} alert(s) to Telegram.")
    finally:
        await app.close()


async def _cmd_refresh(args) -> None:
    app = App()
    try:
        names = [n for n in app.adapters if n.startswith("talabat_")] if args.platform == "talabat" else [args.platform]
        for n in names:
            a = app.adapters.get(n)
            if not hasattr(a, "refresh"):
                sys.exit(f"{n} has no catalogue to refresh.")
            snap = await a.refresh()
            print(f"{a.label}: {len(snap['items'])} items saved at {snap['fetched_at']}")
    finally:
        await app.close()


async def _cmd_talabat_shops(args) -> None:
    from .adapters import list_shops

    s = load_settings()
    o = next((p.options for p in s.platforms.values() if p.options.get("talabat_branch")), {})
    shops = await list_shops(int(o.get("area_id", 8192)), o.get("area_slug", "tagammoa-5-sodic"))
    on = {int(p.options["branch_id"]) for p in s.platforms.values() if p.options.get("talabat_branch")}
    for v in sorted(shops, key=lambda v: v["name"].lower()):
        if "charity" in v["kind"].lower():
            continue
        mark = "*" if v["branch_id"] in on else " "
        print(f"{mark} {v['name'][:28]:28} fee {str(v['delivery_fee']):>5} min {str(v['min_order']):>4}  {v['kind'][:40]}")
        print(f'    {{branch_id = {v["branch_id"]}, slug = "{v["slug"]}", label = "{v["name"]} (talabat)"}},')
    print("\n* = already in config/platforms.toml. Paste lines into [talabat] shops to add more.")


async def _cmd_browser_check(args) -> None:
    import os
    import time

    from .browser import BrowserSession, cdp_info

    s = load_settings()
    url = os.environ.get("BROWSER_CDP_URL", "").strip()
    if not url:
        sys.exit("BROWSER_CDP_URL is empty in .env: shopper launches its own Chrome instead (see README).")
    info = await cdp_info(url)
    if info is None:
        sys.exit(f"Nothing answers at {url}. Start Shopping Chrome: scripts\\windows\\start-shopping-chrome.cmd")
    print(f"Found {info['browser']} with {info['tabs']} open tabs at {url}.")
    t0 = time.monotonic()
    session = BrowserSession(s)
    try:
        page = await session.new_page()
        print(f"Attached in {time.monotonic() - t0:.1f}s.")
        await page.goto("https://www.noon.com/egypt-en/", wait_until="domcontentloaded", timeout=45000)
        print(f"Opened noon in a background tab: {await page.title()!r}")
        await page.close()
    finally:
        await session.close()
    print("OK: Shopping Chrome is ready.")


def ensure_web_token(home: Path) -> str:
    """A random access key for the web page, created once and kept in SHOPPER_HOME."""
    import secrets

    path = home / "web_token"
    if path.exists() and path.read_text().strip():
        return path.read_text().strip()
    home.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(16)
    path.write_text(token)
    return token


async def _cmd_serve(args) -> None:
    import uvicorn

    from .bot import build_bot, setup_menu, watch_loop
    from .web import create_app

    app = App()
    s = app.settings
    if not s.web_token:
        s.web_token = ensure_web_token(s.home)
        print("WEB_TOKEN was empty, so the web page was open to anyone who can reach it. Using the key saved in "
              f"{s.home / 'web_token'} instead; put it in .env as WEB_TOKEN to keep it.")
    server = uvicorn.Server(uvicorn.Config(create_app(app), host=s.web_host, port=s.web_port, log_level="info"))
    bot = None
    loop_task = None
    if s.telegram_token:
        bot = build_bot(app)
        await bot.initialize()
        await setup_menu(bot)
        await bot.start()
        await bot.updater.start_polling()
        loop_task = asyncio.create_task(watch_loop(app, bot, s.watch_interval_hours * 3600))
        print(f"Telegram bot running. Watches re-checked every {s.watch_interval_hours:g} h.")
    else:
        print("TELEGRAM_BOT_TOKEN not set: web page only.")
    print(f"Web page: {s.web_base_url}/{'?t=' + s.web_token if s.web_token else ''}")
    async def warm_talabat() -> None:  # one shop at a time, so talabat sees a polite request rate
        for name, a in app.adapters.items():
            if name.startswith("talabat_") and s.platforms[name].enabled:
                try:
                    await a.snapshot(wait=True)
                except Exception as e:
                    print(f"{a.label}: catalogue download failed: {e}")
    warm = asyncio.create_task(warm_talabat())  # so the first search is fast
    try:
        await server.serve()
    finally:
        if loop_task:
            loop_task.cancel()
        if bot:
            await bot.updater.stop()
            await bot.stop()
            await bot.shutdown()
        await app.close()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="shopper", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("platforms").set_defaults(fn=_cmd_platforms, sync=True)
    p = sub.add_parser("login"); p.add_argument("platform"); p.set_defaults(fn=_cmd_login)
    p = sub.add_parser("capture"); p.add_argument("platform"); p.set_defaults(fn=_cmd_capture)
    for name, fn in (("search", _cmd_search), ("basket", _cmd_basket)):
        p = sub.add_parser(name)
        if name == "search":
            p.add_argument("item")
        else:
            p.add_argument("items", nargs="?")
            p.add_argument("-f", "--file")
        p.add_argument("--exact", action="store_true", help="only exact matches")
        p.add_argument("--max-orders", type=int, default=None)
        p.add_argument("--html", help="also write the comparison table to this file")
        p.add_argument("--platforms", help="comma-separated subset, e.g. gourmet,seoudi")
        p.add_argument("--debug", action="store_true", help="show every platform's top candidates and scores")
        p.add_argument("--push", action="store_true", help="add the best plan to your carts where supported (noon, Amazon)")
        p.set_defaults(fn=fn)
    sub.add_parser("health").set_defaults(fn=_cmd_health)
    sub.add_parser("watch-check").set_defaults(fn=_cmd_watch_check)
    p = sub.add_parser("refresh"); p.add_argument("platform"); p.set_defaults(fn=_cmd_refresh)
    sub.add_parser("talabat-shops").set_defaults(fn=_cmd_talabat_shops)
    sub.add_parser("browser-check").set_defaults(fn=_cmd_browser_check)
    sub.add_parser("serve").set_defaults(fn=_cmd_serve)
    args = ap.parse_args(argv)
    if getattr(args, "sync", False):
        args.fn(args)
    else:
        asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
