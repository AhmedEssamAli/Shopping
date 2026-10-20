"""Local web page: paste a list, get the comparison table. Bind to 127.0.0.1 and reach it
from your phone over Tailscale; links carry WEB_TOKEN so only you can open them."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from html import escape
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import pwa
from .app import App
from .report import page


def _local(iso: str) -> str:
    """Saved UTC time as the PC's local time, like "20 Oct, 03:03"."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b, %H:%M")
    except ValueError:
        return ""


log = logging.getLogger(__name__)
ERRORS = {404: "Page not found", 410: "This comparison is closed", 400: "Something is missing", 500: "Something went wrong"}


def create_app(core: App) -> FastAPI:
    api = FastAPI(title="shopper", docs_url=None, redoc_url=None)
    token = core.settings.web_token

    @api.exception_handler(RequestValidationError)
    async def missing_field(request, exc):
        return await friendly(request, HTTPException(status_code=400, detail="Part of the form was missing. "
                                                     "Go back and try again."))

    @api.exception_handler(StarletteHTTPException)
    async def friendly(request, exc):
        title = ERRORS.get(exc.status_code, "Something went wrong")
        detail = exc.detail if exc.status_code != 404 and isinstance(exc.detail, str) else (
            "Check the link. Links from the bot and the app carry your access key.")
        body = f"<div class='panel plan'><p>{escape(detail)}</p></div>"
        return HTMLResponse(page(title, body, f"<h1>{escape(title)}</h1>"), status_code=exc.status_code)

    async def run_or_explain(requests, **kw):
        try:
            return await core.run(requests, **kw)
        except Exception as e:
            log.exception("compare failed")
            raise HTTPException(status_code=500, detail=f"The comparison failed ({type(e).__name__}: {e}). "
                                "Try again; if it repeats, check that Shopping Chrome is open.") from e

    def check(t: str | None) -> None:
        if token and not (t and secrets.compare_digest(t, token)):
            raise HTTPException(status_code=404)

    def q(t: str | None) -> str:
        return f"?t={quote(t)}" if t else ""

    STATUS = {"live": "Live", "browser": "Through Shopping Chrome", "pending": "Not added yet"}

    def shops_html() -> str:
        groups: dict[str, list[str]] = {}
        for p in core.settings.platforms.values():
            if p.enabled or p.status == "pending":
                groups.setdefault(p.status, []).append(p.label)
        rows = "".join(f"<p class=small><b>{escape(STATUS.get(k, k))}</b><br>{escape(', '.join(sorted(v)))}</p>"
                       for k, v in sorted(groups.items(), key=lambda kv: list(STATUS).index(kv[0]) if kv[0] in STATUS else 9))
        return f"<h2>Shops</h2><div class=panel>{rows}</div>"

    @api.get("/", response_class=HTMLResponse)
    async def home(t: str | None = Query(None)):
        check(t)
        header = (f"<h1>What do you need?</h1><p>shopper checks every shop that delivers to "
                  f"{escape(core.settings.area_name)} and finds the cheapest way to buy it.</p>")
        chosen = core.memory.chosen_lines()
        chips = ("<p class='muted small' style='margin:12px 0 6px'>Products you chose before</p><div class=chips>"
                 + "".join(f"<button type=button class=chip data-line='{escape(line)}' title='{escape(words)}'>{escape(line)}</button>"
                           for line, words in chosen) + "</div>") if chosen else ""
        form = (f"<form method=post action='/compare{q(t)}' class='panel plan'>"
                f"<label for=items><h2 style='margin:0 0 6px'>Type or paste your list</h2></label>"
                f"<p class='muted small' style='margin:0 0 10px'>One item per line, quantity first or last: "
                f"<code>2 Juhayna full cream milk 1L</code> or <code>rice 5kg x1</code>. Arabic works too.</p>"
                f"<textarea id=items name=items required></textarea>{chips}"
                f"<details class=choose><summary>Options</summary>"
                f"<p class=small><label><input type=checkbox name=exact value=1> Exact matches only</label></p>"
                f"<p class=small><label>Most orders to split into <input name=max_orders type=number min=1 max=4 "
                f"value={core.settings.max_orders}></label></p></details>"
                f"<div class=actions><button>Compare prices</button>"
                f"<button class=quiet formaction='/list/add{q(t)}'>Add to the list</button></div></form>")
        items = core.list_items()
        if items:
            rows = "".join(f"<li><span><b class=qty>{it.qty}×</b> {escape(it.query)}</span>"
                           f"<form method=post action='/list/remove{q(t)}' class=inline><input type=hidden name=index value={i}>"
                           f"<input type=hidden name=line value='{escape(it.query)}'>"
                           f"<button class=link>Remove</button></form></li>" for i, it in enumerate(items))
            saved = (f"<h2>The household list</h2><div class=panel><ul class=list>{rows}</ul><div class=actions>"
                     f"<form method=post action='/list/compare{q(t)}' class=inline><button>Compare the list</button></form>"
                     f"<form method=post action='/list/clear{q(t)}' class=inline><button class=quiet>Clear the list</button></form>"
                     f"</div></div>")
        else:
            saved = ("<h2>The household list</h2><div class=panel><p class='muted small' style='margin:0'>Empty. Add lines "
                     "above, send /add in Telegram, or press Save as my list under a result.</p></div>")
        recent = core.store.recent_results()
        past = ("<h2>Recent comparisons</h2><div class=panel><ul class=list>" + "".join(
            f"<li><a href='/r/{escape(rid)}{q(t)}'>{escape(summary or 'Comparison')}</a>"
            f"<span class='muted small'>{_local(when)}</span></li>" for rid, when, summary in recent)
                + "</ul></div>") if recent else ""
        script = ("<script>document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click',()=>{"
                  "const t=document.getElementById('items');t.value=(t.value.trim()?t.value.trim()+'\\n':'')+b.dataset.line;"
                  "t.focus();b.disabled=true}))</script>")
        return pwa.add_head(page("Compare a basket", form + saved + past + shops_html() + script, header), t)

    @api.post("/compare")
    async def run(items: str = Form(...), exact: str | None = Form(None), max_orders: int = Form(2), t: str | None = Query(None)):
        check(t)
        requests = await core.parse_async(items)
        if not requests:
            raise HTTPException(status_code=400, detail="The list is empty. Type at least one item.")
        _, _, rid = await run_or_explain(requests, max_orders=max_orders, exact_only=bool(exact))
        return RedirectResponse(f"/r/{rid}{q(t)}", status_code=303)

    @api.post("/list/{action}")
    async def household_list(action: str, items: str = Form(""), index: int = Form(-1), line: str | None = Form(None),
                             t: str | None = Query(None)):
        check(t)
        if action == "add":
            core.add_to_list(await core.parse_async(items))
        elif action == "remove":
            core.remove_from_list(index, line)  # with the line text, a stale tab cannot remove another line
        elif action == "clear":
            core.clear_list()
        elif action == "compare":
            requests = core.list_items()
            if requests:
                _, _, rid = await run_or_explain(requests)
                return RedirectResponse(f"/r/{rid}{q(t)}", status_code=303)
        else:
            raise HTTPException(status_code=404)
        return RedirectResponse(f"/{q(t)}", status_code=303)

    @api.get("/r/{rid}", response_class=HTMLResponse)
    async def result(rid: str, t: str | None = Query(None)):
        check(t)
        html = core.store.get_result(rid)
        if html is None:
            raise HTTPException(status_code=404)
        result = core.results.get(rid)
        if result is None:  # from history: its buttons would act on a comparison that is gone
            html = re.sub(r"<form method=post action='/r/[^']*'.*?</form>", "", html, flags=re.S)
            note = (f"<p class=notice><b>From history.</b> Prices are from then, and choices need a fresh comparison. "
                    f"<a href='/{q(t)}'>Compare again</a></p>")
            html = html.replace("<main>", "<main>" + note, 1)
        elif rid in core.push_log:
            html = html.replace("<!--cart-push-->", "<p class='muted small'>Added to your carts already: "
                                + escape("; ".join(core.push_log[rid])) + "</p>", 1)
        elif core.pushable(result) and not any(r.needs_pick for r in result.items):
            names = ", ".join(core.adapters[p].label for p in core.pushable(result))
            button = (f"<form method=post action='/r/{escape(rid)}/push{q(t)}' style='margin:16px 0 0'>"
                      f"<button>Add the best plan to my carts ({escape(names)})</button></form>")
            html = html.replace("<!--cart-push-->", button, 1)
        return pwa.add_head(html, t)

    @api.post("/r/{rid}/push")
    async def push(rid: str, t: str | None = Query(None)):
        check(t)
        result = core.results.get(rid)
        if result is None:
            raise HTTPException(status_code=410, detail="This comparison is no longer open. Compare the list again.")
        await core.push_once(rid)
        return RedirectResponse(f"/r/{rid}/pushed{q(t)}", status_code=303)

    @api.get("/r/{rid}/pushed", response_class=HTMLResponse)
    async def pushed(rid: str, t: str | None = Query(None)):
        check(t)
        lines = core.push_log.get(rid)
        if lines is None:
            raise HTTPException(status_code=404)
        body = (f"<div class='panel plan'><ul>{''.join(f'<li>{escape(x)}</li>' for x in lines)}</ul>"
                f"<p><a href='/r/{escape(rid)}{q(t)}'>Back to the comparison</a></p><p><a href='/{q(t)}'>Compare a new list</a></p></div>")
        return pwa.add_head(page("Cart push", body, "<h1>Cart push</h1><p>What went into your carts. Check each cart before you pay.</p>"), t)

    @api.post("/r/{rid}/{action}")
    async def feedback(rid: str, action: str, item: int = Form(...), card: str | None = Form(None),
                       platform: str | None = Form(None), offer: str | None = Form(None), t: str | None = Query(None)):
        check(t)
        if action not in ("pick", "reject", "forget"):
            raise HTTPException(status_code=404)
        if core.feedback(rid, action, item, card, platform, offer) == "gone":
            raise HTTPException(status_code=410, detail="This comparison is no longer open, because shopper "
                                "restarted since. Compare the list again to choose products.")
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
