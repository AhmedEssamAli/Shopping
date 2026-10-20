"""Local web page: paste a list, get the comparison table. Bind to 127.0.0.1 and reach it
from your phone over Tailscale; links carry WEB_TOKEN so only you can open them."""
from __future__ import annotations

import secrets
from html import escape

from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import pwa
from .app import App
from .report import page


def create_app(core: App) -> FastAPI:
    api = FastAPI(title="shopper", docs_url=None, redoc_url=None)
    token = core.settings.web_token

    def check(t: str | None) -> None:
        if token and not (t and secrets.compare_digest(t, token)):
            raise HTTPException(status_code=404)

    def q(t: str | None) -> str:
        return f"?t={escape(t)}" if t else ""

    @api.get("/", response_class=HTMLResponse)
    async def home(t: str | None = Query(None)):
        check(t)
        body = (f"<h1>Compare a basket</h1><p class=muted>One item per line. Quantity first or last: "
                f"<code>2 Juhayna full cream milk 1L</code>, <code>rice 5kg x1</code>. Delivery: {escape(core.settings.area_name)}.</p>"
                f"<form method=post action='/compare{q(t)}'><textarea name=items required></textarea>"
                f"<p><label><input type=checkbox name=exact value=1> exact matches only</label> · "
                f"max orders <input name=max_orders type=number min=1 max=4 value={core.settings.max_orders} style='width:3em'></p>"
                f"<button>Compare</button></form>")
        return pwa.add_head(page("Compare a basket", body), t)

    @api.post("/compare")
    async def run(items: str = Form(...), exact: str | None = Form(None), max_orders: int = Form(2), t: str | None = Query(None)):
        check(t)
        requests = core.parse(items)
        if not requests:
            raise HTTPException(status_code=400, detail="Empty list")
        _, _, rid = await core.run(requests, max_orders=max_orders, exact_only=bool(exact))
        return RedirectResponse(f"/r/{rid}{q(t)}", status_code=303)

    @api.get("/r/{rid}", response_class=HTMLResponse)
    async def result(rid: str, t: str | None = Query(None)):
        check(t)
        html = core.store.get_result(rid)
        if html is None:
            raise HTTPException(status_code=404)
        result = core.results.get(rid)
        pushable = [core.adapters[p].label for p in (result.best.platforms if result and result.best else [])
                    if p in core.adapters and core.adapters[p].supports_cart_push]
        if pushable:
            button = (f"<form method=post action='/r/{escape(rid)}/push{q(t)}' style='margin:16px 0'>"
                      f"<button>Add the best plan to my carts ({escape(', '.join(pushable))})</button></form>")
            html = html.replace("</main>", button + "</main>", 1)
        return pwa.add_head(html, t)

    @api.post("/r/{rid}/push", response_class=HTMLResponse)
    async def push(rid: str, t: str | None = Query(None)):
        check(t)
        result = core.results.get(rid)
        if result is None:
            raise HTTPException(status_code=404, detail="Compare again; this result is no longer in memory")
        lines = await core.push_carts(result)
        body = (f"<h1>Cart push</h1><ul>{''.join(f'<li>{escape(x)}</li>' for x in lines)}</ul>"
                f"<p><a href='/r/{escape(rid)}{q(t)}'>Back to the comparison</a> · <a href='/{q(t)}'>New basket</a></p>")
        return pwa.add_head(page("Cart push", body), t)

    @api.post("/r/{rid}/{action}")
    async def feedback(rid: str, action: str, item: int = Form(...), card: int | None = Form(None),
                       platform: str | None = Form(None), t: str | None = Query(None)):
        check(t)
        if action not in ("pick", "reject", "forget"):
            raise HTTPException(status_code=404)
        if not core.feedback(rid, action, item, card, platform):
            raise HTTPException(status_code=404, detail="Compare again; this result is no longer in memory")
        return RedirectResponse(f"/r/{rid}{q(t)}", status_code=303)

    @api.get("/manifest.webmanifest")
    async def manifest(t: str | None = Query(None)):
        check(t)  # the manifest carries the token in start_url
        return Response(pwa.manifest(t), media_type="application/manifest+json")

    @api.get("/sw.js")
    async def service_worker():
        return Response(pwa.SERVICE_WORKER, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @api.get("/offline", response_class=HTMLResponse)
    async def offline():
        return page("shopper is offline", pwa.OFFLINE_BODY)

    @api.get("/icon-{size}.png")
    async def icon(size: int):
        if size not in (192, 512):
            raise HTTPException(status_code=404)
        return Response(pwa.icon_png(size), media_type="image/png", headers={"Cache-Control": "max-age=604800"})

    return api
