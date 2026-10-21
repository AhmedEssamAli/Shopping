"""Local web page: paste a list, get the comparison table. Bind to 127.0.0.1 and reach it
from your phone over Tailscale; links carry WEB_TOKEN so only you can open them."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from html import escape
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import pwa
from .app import App
from .models import now_utc
from .entry import compose, entry_of
from .report import ROW_LISTS, fields_html, item_row, page, stepper, steps_html


def _local(iso: str) -> str:
    """Saved UTC time as the PC's local time, like "20 Oct, 03:03"."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b, %H:%M")
    except ValueError:
        return ""


log = logging.getLogger(__name__)
ERRORS = {404: "Page not found", 410: "This comparison is closed", 400: "Something is missing", 500: "Something went wrong"}


# The item rows: "Searches for" preview, − / + quantity, add and remove rows, chips into the paste box.
FORM_SCRIPT = ("<script>const prev=r=>{const v=n=>(r.querySelector(`[name=${n}]`).value||'').trim();"
               "const item=v('item'),low=item.toLowerCase(),size=v('amount')&&v('unit')?v('amount')+v('unit'):'',"
               "n=parseInt(v('pieces'))||1,qty=parseInt(v('qty'))||1;"
               "const w=[v('brand')&&!low.includes(v('brand').toLowerCase())?v('brand'):'',"
               "v('kind')&&!low.includes(v('kind').toLowerCase())?v('kind'):'',item,n>1?(size?n+'x'+size:n+' pcs'):size];"
               "const p=r.querySelector('.preview');p.textContent='';if(item){p.append('Searches for: ');"
               "const b=document.createElement('b');b.textContent=qty+' × '+w.filter(Boolean).join(' ');p.append(b)}};"
               "const rows=document.getElementById('rows'),count=()=>{const c=document.getElementById('count');"
               "if(c)c.textContent=[...rows.querySelectorAll('[name=item]')].filter(i=>i.value.trim()).length};"
               "rows.querySelectorAll('.itemrow').forEach(prev);"
               "rows.addEventListener('input',e=>{const r=e.target.closest('.itemrow');if(r){prev(r);count()}});"
               "rows.addEventListener('change',e=>{const r=e.target.closest('.itemrow');if(r)prev(r)});"
               "rows.addEventListener('click',e=>{const d=e.target.closest('.droprow');if(d){const r=d.closest('.itemrow');"
               "if(rows.querySelectorAll('.itemrow').length>1)r.remove();else r.querySelectorAll('input,select').forEach(i=>i.value='');"
               "count();return}const b=e.target.closest('.inc,.dec');if(!b)return;"
               "const i=b.parentElement.querySelector('input'),n=(parseInt(i.value)||1)+(b.classList.contains('inc')?1:-1);"
               "i.value=Math.max(1,n);prev(b.closest('.itemrow'))});"
               "document.getElementById('addrow').addEventListener('click',()=>{const r=rows.querySelector('.itemrow')"
               ".cloneNode(true);r.querySelectorAll('input,select').forEach(i=>i.value='');r.querySelector('.preview').textContent='';"
               "rows.append(r);r.querySelector('input').focus()});"
               "document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click',()=>{"
               "const t=document.getElementById('items');t.value=(t.value.trim()?t.value.trim()+'\\n':'')+b.dataset.line;"
               "t.focus();b.disabled=true}))</script>")


def _rows(*cols: list[str]) -> list[list[str]]:
    """Form columns padded to the item column's length, so a missing field reads as empty."""
    n = len(cols[0])
    return [list(c) + [""] * (n - len(c)) for c in cols]


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

    STATUS = {"live": "Live", "browser": "Through Shopping Chrome", "app": "From the app: screenshots or typed prices",
              "pending": "Not added yet"}

    def app_prices_html(t: str | None) -> str:
        shops = {n: p.label for n, p in core.settings.platforms.items() if p.status == "app" and p.enabled}
        if not shops:
            return ""
        options = "".join(f"<option value='{escape(n)}'>{escape(lbl)}</option>" for n, lbl in shops.items())
        rows = "".join(
            f"<li><span><b>{escape(shops.get(p, p))}</b> {escape(name)}</span><span><span class=num>{price:,.2f}</span> "
            f"<span class='muted small'>{_local(at.isoformat())}</span> "
            f"<form method=post action='/prices/delete{q(t)}' class=inline><input type=hidden name=entry value={i}>"
            f"<button class=link>Delete</button></form></span></li>"
            for i, p, name, price, at in core.prices.latest()[:12])
        return (f"<h2 id=prices>Prices from the apps</h2><form method=post action='/prices/add{q(t)}' class=panel>"
                f"<p class='muted small' style='margin:0 0 8px'>Breadfast and Rabbit have no website catalogue. Type what "
                f"the app shows, one per line, like <code>Juhayna skimmed milk 1L = 54.95</code>. Comparisons use these "
                f"prices for 30 days.</p><p><label>Shop <select name=shop>{options}</select></label></p>"
                f"<textarea name=lines style='min-height:110px' required></textarea>"
                f"<div class=actions><button>Save prices</button></div></form>"
                f"<form method=post action='/prices/screenshot{q(t)}' enctype=multipart/form-data class=panel>"
                f"<p class='muted small' style='margin:0 0 8px'><b>Faster:</b> take a screenshot of the app's search "
                f"results and upload it. shopper reads each product and price (Claude, needs ANTHROPIC_API_KEY). "
                f"In Telegram, just send the screenshot with the caption <code>breadfast</code> or <code>rabbit</code>.</p>"
                f"<p><label>Shop <select name=shop>{options}</select></label></p>"
                f"<p><input type=file name=image accept='image/*' required></p>"
                f"<div class=actions><button>Read the screenshot</button></div></form>"
                + (f"<div class=panel><ul class=list>{rows}</ul></div>" if rows else ""))

    async def areas_html(t: str | None) -> str:
        """Choose the district for shops that price by branch (Seoudi), from the shop's own area list."""
        parts = []
        for name, a in core.adapters.items():
            if not hasattr(a, "areas") or not getattr(a, "area_cities", ()):
                continue
            try:
                areas = await a.areas()
            except Exception as e:  # noqa: BLE001 - the page still works without the picker
                log.warning("%s areas: %s", a.label, e)
                continue
            groups: dict[str, list[str]] = {}
            for x in areas:
                where = f" — {x['store']} branch" if x["store"] else " — no branch"
                sel = " selected" if a.area == x["district"] else ""
                groups.setdefault(x["city"], []).append(
                    f"<option value='{escape(x['id'])}'{sel}>{escape(x['district'] + where)}</option>")
            opts = "".join(f"<optgroup label='{escape(c)}'>{''.join(v)}</optgroup>" for c, v in groups.items())
            now = (f"Now: <b>{escape(a.area)}</b>, {escape(a.branch + ' branch') if a.branch else 'no branch (default store)'}."
                   if a.area else f"Now: found from your map point{', ' + escape(a.branch) + ' branch' if a.branch else ''}. "
                   f"Choose your area to be sure.")
            parts.append(f"<form method=post action='/area/{escape(name)}{q(t)}' class=panel>"
                         f"<p class=small style='margin:0 0 8px'><b>{escape(a.label)}</b> prices and stock depend on the branch "
                         f"that serves your area. {now}</p><p><select name=district aria-label='{escape(a.label)} area' required>"
                         f"<option value=''>Choose your area</option>{opts}</select></p>"
                         f"<div class=actions><button>Use this area</button></div></form>")
        return ("<h2 id=area>Your delivery area</h2>" + "".join(parts)) if parts else ""

    def shops_html() -> str:
        groups: dict[str, list[str]] = {}
        for p in core.settings.platforms.values():
            if p.enabled or p.status == "pending":  # the page shows what is on and what is missing
                groups.setdefault(p.status, []).append(p.label)
        rows = "".join(f"<p class=small><b>{escape(STATUS.get(k, k))}</b><br>{escape(', '.join(sorted(v)))}</p>"
                       for k, v in sorted(groups.items(), key=lambda kv: list(STATUS).index(kv[0]) if kv[0] in STATUS else 9))
        return f"<h2>Shops</h2><div class=panel>{rows}</div>"

    @api.get("/", response_class=HTMLResponse)
    async def home(t: str | None = Query(None)):
        check(t)
        header = (f"<span class=eyebrow>Groceries delivered to {escape(core.settings.area_name)}</span>"
                  f"<h1>What do you need?</h1><p>Add your items. shopper checks every shop that delivers to you "
                  f"and finds the cheapest way to buy them, after fees.</p>{steps_html(1)}")
        chosen = core.memory.chosen_lines()
        chips = ("<p class='muted small' style='margin:12px 0 6px'>Products you chose before</p><div class=chips>"
                 + "".join(f"<button type=button class=chip data-line='{escape(line)}' title='{escape(words)}'>{escape(line)}</button>"
                           for line, words in chosen) + "</div>") if chosen else ""
        guide = ("<details class=choose><summary>How shopper reads an item</summary><div class=guide>"
                 "<b>Item</b><span>What it is: milk, rice, instant coffee. Required.</span>"
                 "<b>Brand</b><span>Only this brand counts as exact. Empty: any brand, cheapest wins.</span>"
                 "<b>Type</b><span>Variant, flavour or grade: skimmed, full cream, basmati, classic. Empty: shopper "
                 "still keeps other variants (chocolate, lactose free) out of exact matches.</span>"
                 "<b>Size of one</b><span>One piece: 1 L, 200 ml, 5 kg. Empty: any size; you choose from the cards.</span>"
                 "<b>Pieces in pack</b><span>6 for a 6-pack of 1 L; 30 for a tray of eggs. Empty: single piece.</span>"
                 "<b>How many</b><span>Packs to buy. Empty: 1.</span></div>"
                 "<p class='muted small'>Typed and pasted lines follow the same order: how many, brand, type, item, "
                 "pieces x size. <code>2 Juhayna skimmed milk 6x1L</code> is 2 six-packs of 1 L Juhayna skimmed milk.</p>"
                 "</details>")
        form = (f"<form method=post action='/compare{q(t)}' class='panel plan'>"
                f"<h2 style='margin:0 0 6px'>What do you want to buy?</h2>"
                f"<p class='muted small' style='margin:0 0 10px'>One item per row. Only the item is required. Every field "
                f"you fill must match; every field you leave empty is open.</p>"
                f"<div id=rows>{item_row() * 2}</div>{ROW_LISTS}{past_list()}<button type=button class=quiet id=addrow>Add another item</button>{guide}"
                f"<details class=choose{' open' if chosen else ''}><summary>Or paste a list</summary>"
                f"<p class='muted small' style='margin:8px 0'>One item per line, quantity first or last: "
                f"<code>2 Juhayna full cream milk 1L</code> or <code>rice 5kg x1</code>. Arabic works too.</p>"
                f"<textarea id=items name=items></textarea>{chips}</details>"
                f"<details class=choose><summary>Options</summary>"
                f"<p class=small><label><input type=checkbox name=exact value=1> Exact matches only</label></p>"
                f"<p class=small><label>Most orders to split into <input name=max_orders type=number min=1 max=4 "
                f"value={core.settings.max_orders}></label></p></details>"
                f"<div class=actions><button>Compare prices</button>"
                f"<button class=quiet formaction='/list/add{q(t)}'>Add to the list</button></div></form>")
        items = core.list_items()
        if items:
            rows = "".join(f"<li id=list-{i}><span class=listline>{stepper(f'/list/qty', q(t), it.qty, {'index': i, 'line': it.query}, it.query)}"
                           f"<span>{escape(it.query)}{fields_html(it.query)}</span></span>"
                           f"<form method=post action='/list/remove{q(t)}' class=inline><input type=hidden name=index value={i}>"
                           f"<input type=hidden name=line value='{escape(it.query)}'>"
                           f"<button class=link>Remove</button></form></li>" for i, it in enumerate(items))
            saved = (f"<h2>The household list</h2><div class=panel><ul class=list>{rows}</ul><div class=actions>"
                     f"<form method=post action='/list/compare{q(t)}' class=inline><button>Compare the list</button></form>"
                     f"<form method=post action='/list/clear{q(t)}' class=inline><button class=quiet>Clear the list</button></form>"
                     f"</div><form method=post action='/lists/save{q(t)}' class=savelist><label>Keep a copy as"
                     f"<input name=name list=listnames placeholder='Weekly' required maxlength=40></label>"
                     f"<button class=quiet>Save list</button></form></div>")
        else:
            saved = ("<h2>The household list</h2><div class=panel><p class='muted small' style='margin:0'>Empty. Add lines "
                     "above, send /add in Telegram, or press Save as my list under a result.</p></div>")
        named = core.named_lists()
        names = "<datalist id=listnames>" + "".join(f"<option value='{escape(nm)}'>" for nm, _ in named) + "</datalist>"
        saved += names + (("<h2 id=lists>Saved lists</h2><div class=panel><ul class=list>" + "".join(
            f"<li><span><b>{escape(nm)}</b> <span class='muted small'>{k} line{'s' if k != 1 else ''}</span></span>"
            f"<span class=rowactions>"
            + "".join(f"<form method=post action='/lists/{act}{q(t)}' class=inline><input type=hidden name=name value='{escape(nm)}'>"
                      f"<button class='{cls}'>{txt}</button></form>"
                      for act, txt, cls in (("review", "Review and compare", "small"), ("use", "Make it my list", "quiet small"),
                                            ("delete", "Delete", "link")))
            + "</span></li>" for nm, k in named) + "</ul></div>") if named else "")
        recent = core.store.recent_results()
        past = ("<h2>Recent comparisons</h2><div class=panel><ul class=list>" + "".join(
            f"<li><a href='/r/{escape(rid)}{q(t)}'>{escape(summary or 'Comparison')}</a>"
            f"<span class=rowactions><span class='muted small'>{_local(when)}</span>"
            + (f"<form method=post action='/r/{escape(rid)}/reorder{q(t)}' class=inline><button class='quiet small'>Buy again</button></form>"
               if rid in core.results else "") + "</span></li>" for rid, when, summary in recent)
                + "</ul></div>") if recent else ""
        return pwa.add_head(page("Compare a basket", form + saved + past + await areas_html(t) + app_prices_html(t) + shops_html() + FORM_SCRIPT, header), t)

    @api.post("/prices/screenshot")
    async def app_screenshot(shop: str = Form(""), image: UploadFile = File(...), t: str | None = Query(None)):
        check(t)
        import asyncio

        from .adapters.appshop import read_screenshot

        plat = core.settings.platforms.get(shop)
        if not plat or plat.status != "app":
            raise HTTPException(status_code=400, detail="Choose Breadfast or Rabbit.")
        data = await image.read()
        if not data or len(data) > 10_000_000 or not (image.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="Upload a screenshot (an image under 10 MB).")
        try:
            entries = await asyncio.to_thread(read_screenshot, data, image.content_type, plat.label,
                                              core.settings.anthropic_key)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read the screenshot: {e}") from e
        if not entries:
            raise HTTPException(status_code=400, detail="No products with prices on that screenshot. "
                                "Upload the search results screen.")
        core.prices.add(shop, entries)
        return RedirectResponse(f"/{q(t)}#prices", status_code=303)

    @api.post("/prices/{action}")
    async def app_prices(action: str, shop: str = Form(""), lines: str = Form(""), entry: int = Form(-1),
                         t: str | None = Query(None)):
        check(t)
        from .adapters.appshop import parse_prices

        if action == "add" and shop in core.settings.platforms and core.settings.platforms[shop].status == "app":
            core.prices.add(shop, parse_prices(lines))
        elif action == "delete":
            core.prices.delete(entry)
        else:
            raise HTTPException(status_code=404)
        return RedirectResponse(f"/{q(t)}#prices", status_code=303)

    @api.post("/area/{shop}")
    async def area(shop: str, district: str = Form(""), t: str | None = Query(None)):
        check(t)
        if not await core.set_area(shop, district):
            raise HTTPException(status_code=400, detail="Choose an area from the list.")
        return RedirectResponse(f"/{q(t)}#area", status_code=303)

    def past_list() -> str:
        """Lines you chose a product for, and list lines, as suggestions for the item field."""
        seen = dict.fromkeys([line for line, _ in core.memory.chosen_lines(30)] + [i.query for i in core.list_items()]
                             + [n.query for name, _ in core.named_lists() for n in core.named(name)])
        return "<datalist id=past>" + "".join(f"<option value='{escape(x)}'>" for x in list(seen)[:60]) + "</datalist>"

    def review_page(requests, t: str | None, max_orders: int, exact: bool) -> HTMLResponse:
        """Step 2: every line as editable fields, before any shop is searched."""
        n = len(requests)
        core.store.log("review", "", str(n))
        header = (f"<span class=eyebrow>Nothing searched yet</span><h1>Check your list</h1>"
                  f"<p>shopper read {n} line{'s' if n != 1 else ''} like this. Fix anything that is wrong, "
                  f"change quantities, or remove lines. Then compare.</p>{steps_html(2)}")
        form = (f"<form method=post action='/compare{q(t)}' class='panel plan'>"
                f"<div id=rows>{''.join(item_row(entry_of(r)) for r in requests)}</div>{ROW_LISTS}{past_list()}"
                f"<button type=button class=quiet id=addrow>Add another item</button>"
                f"<input type=hidden name=max_orders value={int(max_orders)}>"
                + ("<input type=hidden name=exact value=1>" if exact else "")
                + f"<div class=actions><button>Compare <span id=count>{n}</span> items</button>"
                f"<button class=quiet formaction='/list/save{q(t)}'>Save as my list</button>"
                f"<a class=back href='/{q(t)}'>Back</a></div></form>")
        return HTMLResponse(pwa.add_head(page("Check your list", form + FORM_SCRIPT, header), t))

    async def requests_from(items: str, rows: list[list[str]]) -> list:
        """Rows of the item form first, then any pasted lines. Rows: item, brand, type, amount, unit, pieces, qty."""
        out = []
        for item, brand, kind, amount, unit, pieces, qty in zip(*rows) if rows and rows[0] else ():
            e = compose(item, brand, kind, f"{amount.strip()}{unit}" if amount.strip() and unit else "", pieces, qty)
            if e:
                out.append(e.request())
        return out + (await core.parse_async(items) if items.strip() else [])

    @api.post("/compare")
    async def run(items: str = Form(""), exact: str | None = Form(None), max_orders: int = Form(2),
                  item: list[str] = Form([]), brand: list[str] = Form([]), kind: list[str] = Form([]),
                  amount: list[str] = Form([]), unit: list[str] = Form([]), pieces: list[str] = Form([]),
                  qty: list[str] = Form([]), t: str | None = Query(None)):
        check(t)
        requests = await requests_from(items, _rows(item, brand, kind, amount, unit, pieces, qty))
        if not requests:
            raise HTTPException(status_code=400, detail="The list is empty. Fill in at least one item.")
        if items.strip():  # a pasted list is checked line by line before any search
            return review_page(requests, t, max_orders, bool(exact))
        _, _, rid = await run_or_explain(requests, max_orders=max_orders, exact_only=bool(exact))
        return RedirectResponse(f"/r/{rid}{q(t)}", status_code=303)

    @api.post("/list/{action}")
    async def household_list(action: str, items: str = Form(""), index: int = Form(-1), line: str | None = Form(None),
                             item: list[str] = Form([]), brand: list[str] = Form([]), kind: list[str] = Form([]),
                             amount: list[str] = Form([]), unit: list[str] = Form([]), pieces: list[str] = Form([]),
                             qty: list[str] = Form([]), delta: str = Form("0"), t: str | None = Query(None)):
        check(t)
        if action == "add":
            core.add_to_list(await requests_from(items, _rows(item, brand, kind, amount, unit, pieces, qty)))
        elif action == "remove":
            core.remove_from_list(index, line)  # with the line text, a stale tab cannot remove another line
        elif action == "clear":
            core.clear_list()
        elif action == "qty":
            try:
                n = int(qty[0] if qty else "0") + int(delta or 0)
            except ValueError:
                raise HTTPException(status_code=400, detail="The quantity must be a whole number.") from None
            core.set_list_qty(index, n, line)
        elif action == "compare":
            requests = core.list_items()
            if requests:
                return review_page(requests, t, core.settings.max_orders, False)
        elif action == "save":  # from the review step: the checked rows become the household list
            requests = await requests_from("", _rows(item, brand, kind, amount, unit, pieces, qty))
            if requests:
                core.save_list(requests)
        else:
            raise HTTPException(status_code=404)
        return RedirectResponse(f"/{q(t)}#list-{index}" if action == "qty" else f"/{q(t)}", status_code=303)

    @api.get("/r/{rid}", response_class=HTMLResponse)
    async def result(rid: str, t: str | None = Query(None)):
        check(t)
        html = core.store.get_result(rid)
        if html is None:
            raise HTTPException(status_code=404)
        result = core.results.get(rid)
        if result is None:  # from history: its buttons would act on a comparison that is gone
            html = re.sub(r"<form method=post action='/r/[^']*'.*?</form>", "", html, flags=re.S)
            html = html.replace("<div class=notice></div>", "")  # the Undo notice lived inside its form
            note = (f"<p class=notice><b>From history.</b> Prices are from then, and choices need a fresh comparison. "
                    f"<a href='/{q(t)}'>Compare again</a></p>")
            html = html.replace("<main>", "<main>" + note, 1)
        elif (now_utc() - result.created_at).total_seconds() > 30 * 60:
            html = html.replace("<!--refresh-->", f"<div class=notice><form method=post action='/r/{escape(rid)}/refresh{q(t)}' "
                                f"class=inline><b>Prices are over 30 minutes old.</b> <button class=link>Refresh prices</button>"
                                f"</form></div>", 1)
        if result is not None and rid in core.push_log:
            html = html.replace("<!--cart-push-->", "<p class='muted small'>Added to your carts already: "
                                + escape("; ".join(core.push_log[rid])) + "</p>", 1)
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

    def gone(status: str) -> None:
        if status == "gone":
            raise HTTPException(status_code=410, detail="This comparison is no longer open, because shopper "
                                "restarted since. Compare the list again to change it.")

    def new_qty(qty: str, delta: str) -> int | None:
        if not qty.strip():
            return None  # an emptied box changes nothing; "Remove line" removes
        try:
            return int(qty.strip()) + int(delta or 0)
        except ValueError:
            raise HTTPException(status_code=400, detail="The quantity must be a whole number.") from None

    @api.post("/lists/{action}")
    async def named_lists(action: str, name: str = Form(""), t: str | None = Query(None)):
        check(t)
        if action == "save":
            if not core.save_named(name):
                raise HTTPException(status_code=400, detail="Give the list a name, and add lines to the household list first.")
        elif action == "review":
            items = core.named(name)
            if not items:
                raise HTTPException(status_code=404)
            return review_page(items, t, core.settings.max_orders, False)
        elif action == "use":
            core.use_named(name)
        elif action == "delete":
            core.delete_named(name)
        else:
            raise HTTPException(status_code=404)
        return RedirectResponse(f"/{q(t)}#lists", status_code=303)

    @api.post("/r/{rid}/reorder")
    async def reorder(rid: str, t: str | None = Query(None)):
        """Buy again: that comparison's lines, with their quantities, in the review step."""
        check(t)
        result = core.results.get(rid)
        if result is None:
            gone("gone")
        return review_page([r.request for r in result.items], t, result.max_orders or core.settings.max_orders,
                           result.exact_only)

    @api.post("/r/{rid}/research")
    async def research(rid: str, index: int = Form(...), line: str = Form(""), item: str = Form(""), brand: str = Form(""),
                       kind: str = Form(""), amount: str = Form(""), unit: str = Form(""), pieces: str = Form(""),
                       qty: str = Form(""), t: str | None = Query(None)):
        """A line's "Search again": its adjusted fields, searched in every shop of this comparison."""
        check(t)
        e = compose(item, brand, kind, f"{amount.strip()}{unit}" if amount.strip() and unit else "", pieces, qty)
        if e is None:
            raise HTTPException(status_code=400, detail="The item field is empty. Go back and fill it in.")
        gone(await core.research_line(rid, index, line or None, e.request()))
        return RedirectResponse(f"/r/{rid}{q(t)}#line-{index}", status_code=303)

    @api.post("/r/{rid}/push-shop")
    async def push_shop(rid: str, shop: str = Form(...), t: str | None = Query(None)):
        check(t)
        if rid not in core.results:
            gone("gone")
        await core.push_shop(rid, shop)
        return RedirectResponse(f"/r/{rid}{q(t)}#plan", status_code=303)

    @api.post("/r/{rid}/done")
    async def order_done(rid: str, shop: str = Form(...), done: int = Form(1), t: str | None = Query(None)):
        check(t)
        if not core.mark_done(rid, shop, bool(done)):
            gone("gone")
        return RedirectResponse(f"/r/{rid}{q(t)}#plan", status_code=303)

    @api.post("/r/{rid}/qty")
    async def line_qty(rid: str, item: int = Form(...), line: str = Form(""), qty: str = Form("1"), delta: str = Form("0"),
                       t: str | None = Query(None)):
        check(t)
        status = core.edit_line(rid, item, line or None, qty=new_qty(qty, delta))
        gone(status)
        return RedirectResponse(f"/r/{rid}{q(t)}#line-{item}", status_code=303)

    @api.post("/r/{rid}/remove")
    async def line_remove(rid: str, item: int = Form(...), line: str = Form(""), t: str | None = Query(None)):
        check(t)
        gone(core.edit_line(rid, item, line or None, remove=True))
        return RedirectResponse(f"/r/{rid}{q(t)}", status_code=303)

    @api.post("/r/{rid}/undo")
    async def line_undo(rid: str, t: str | None = Query(None)):
        check(t)
        core.undo_remove(rid)
        return RedirectResponse(f"/r/{rid}{q(t)}", status_code=303)

    @api.post("/r/{rid}/orders")
    async def orders_limit(rid: str, limit: int = Form(...), t: str | None = Query(None)):
        check(t)
        if rid not in core.results:
            gone("gone")
        core.set_orders(rid, limit)
        return RedirectResponse(f"/r/{rid}{q(t)}#plan", status_code=303)

    @api.post("/r/{rid}/refresh")
    async def refresh(rid: str, t: str | None = Query(None)):
        check(t)
        result = core.results.get(rid)
        if result is None:
            gone("gone")
        _, _, new = await run_or_explain([r.request for r in result.items], max_orders=result.max_orders,
                                         exact_only=result.exact_only)
        return RedirectResponse(f"/r/{new}{q(t)}", status_code=303)

    @api.post("/r/{rid}/{action}")
    async def feedback(rid: str, action: str, item: int = Form(...), card: str | None = Form(None),
                       platform: str | None = Form(None), offer: str | None = Form(None), line: str | None = Form(None),
                       t: str | None = Query(None)):
        check(t)
        if action not in ("pick", "reject", "forget", "confirm", "deny"):
            raise HTTPException(status_code=404)
        if core.feedback(rid, action, item, card, platform, offer, line) == "gone":
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
