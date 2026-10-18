"""Local web page: paste a list, get the comparison table. Bind to 127.0.0.1 and reach it
from your phone over Tailscale; links carry WEB_TOKEN so only you can open them."""
from __future__ import annotations

import secrets
from html import escape

from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

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
        return page("Compare a basket", body)

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
        return html

    return api
