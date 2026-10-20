"""Render a CompareResult as chat text (Telegram/CLI) or as an HTML comparison table."""
from __future__ import annotations

from html import escape

from .adapters import Adapter
from .config import Settings
from .models import Proposal
from .service import CompareResult


def _per(unit: str) -> str:
    return {"ml": "L", "g": "kg"}.get(unit, "pc")


def tip_line(settings: Settings, r) -> str | None:
    t = r.tip
    if not t:
        return None
    u = _per(t.offer.size.unit)
    return (f"{t.offer.name} at {label(settings, t.offer.platform)} is {t.unit_price:,.1f}/{u} "
            f"vs {t.baseline:,.1f}/{u} (−{t.saving_pct:.0f}%)")


def snapshot_ages(result: CompareResult, older_than_min: int = 30) -> dict[str, str]:
    """Platforms whose offers were fetched well before this comparison (catalogue snapshots)."""
    out = {}
    for r in result.items:
        for p, offers in r.candidates.items():
            if offers and (result.created_at - offers[0].fetched_at).total_seconds() > older_than_min * 60:
                out[p] = offers[0].fetched_at.astimezone().strftime("%d %b %H:%M")
    return out


def _egp(x: float) -> str:
    return f"EGP {x:,.2f}"


def label(settings: Settings, name: str) -> str:
    p = settings.platforms.get(name)
    return p.label if p else name


def cart_links(result: CompareResult, prop: Proposal, adapters: dict[str, Adapter]) -> dict[str, str]:
    out = {}
    for platform in prop.platforms:
        lines = [(m.offer, m.request.qty) for m in prop.assignments.values() if m.offer.platform == platform]
        link = adapters[platform].cart_link(lines) if platform in adapters else None
        if link:
            out[platform] = link
    return out


def _plan_lines(result: CompareResult, prop: Proposal, settings: Settings) -> list[str]:
    lines = []
    for platform in prop.platforms:
        lines.append(f"{label(settings, platform)} — {_egp(prop.subtotals[platform])} + fees {_egp(prop.fees[platform])}")
        for i, m in sorted(prop.assignments.items()):
            if m.offer.platform != platform:
                continue
            tag = "" if m.exact else " [close match]"
            if result.items[i].needs_pick:
                tag += " [choose your product in the table]"
            lines.append(f"  • {m.request.qty} × {m.offer.name} @ {m.offer.price:,.2f}{tag}\n    {m.offer.url}")
    return lines


def to_text(result: CompareResult, settings: Settings, adapters: dict[str, Adapter], table_url: str | None = None) -> str:
    n = len(result.items)
    out = [f"{n} item{'s' if n != 1 else ''} · {settings.area_name} · searched {', '.join(label(settings, p) for p in result.platforms)}"]
    best = result.best
    if not best:
        out.append("No platform had an acceptable match.")
    else:
        names = " + ".join(label(settings, p) for p in best.platforms)
        out.append(f"\nBEST PLAN: {names} — {_egp(best.total)}")
        pending = [r.request.query for r in result.items if r.needs_pick]
        if pending:
            out.append(f"Not final: several products fit {len(pending)} line(s). Choose yours in the Full table: "
                       + "; ".join(pending))
        out += _plan_lines(result, best, settings)
        for platform, link in cart_links(result, best, adapters).items():
            out.append(f"  Cart link ({label(settings, platform)}): {link}")
        single = result.single
        if single and single is not best:
            diff = single.total - best.total
            out.append(f"\nBest single platform: {label(settings, single.platforms[0])} — {_egp(single.total)} "
                       f"({'+' if diff >= 0 else ''}{diff:,.2f} vs best plan)")
        if best.missing:
            out.append("\nNot found: " + "; ".join(result.items[i].request.query for i in best.missing))
        tips = [(r, tip_line(settings, r)) for r in result.items if r.tip]
        if tips:
            out.append("\nBetter-value packs:")
            out += [f"  • {r.request.query}: {line}\n    {r.tip.offer.url}" for r, line in tips]
    if result.unverified_fees:
        out.append("\nFees not verified (counted as 0): " + ", ".join(label(settings, p) for p in result.unverified_fees))
    for p, err in result.errors.items():
        out.append(f"⚠ {label(settings, p)}: {err}")
    out.append(f"Prices fetched {result.created_at.astimezone().strftime('%H:%M')}. Check the total in the app before paying.")
    out += [f"{label(settings, p)} prices are from its catalogue snapshot of {t}." for p, t in snapshot_ages(result).items()]
    if table_url:
        out.append(f"\nFull table: {table_url}")
    return "\n".join(out)


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--best:#e3f2e6;--best-fg:#14532d;--close:#8a5a00;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#9c9a93;--line:#2e2d2a;--best:#163322;--best-fg:#bfe8cc;--close:#e0b45a;--card:#1d1d1a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px}h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}
.muted{color:var(--muted)}.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:640px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
th{font-weight:600;font-size:13px;color:var(--muted)}td.best{background:var(--best);color:var(--best-fg)}
.price{font-weight:650;font-variant-numeric:tabular-nums}.name{font-size:13px}.close{color:var(--close);font-size:12px}
a{color:inherit}.plan{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.plan b{font-variant-numeric:tabular-nums}ul{margin:6px 0 0;padding-left:18px}.warn{color:var(--close)}
textarea{width:100%;min-height:160px;font:inherit;padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
button{font:inherit;padding:8px 16px;border-radius:8px;border:1px solid var(--line);background:var(--fg);color:var(--bg);cursor:pointer}
form.inline{display:inline;margin:0}button.link{background:none;border:0;padding:2px 0;color:var(--muted);text-decoration:underline;font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;margin:8px 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.card ul{font-size:13px;margin-bottom:8px}
h3{font-size:15px;margin:16px 0 4px}details{margin:8px 0}summary{cursor:pointer;color:var(--muted)}
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")


def _plan_html(result: CompareResult, prop: Proposal, settings: Settings, adapters: dict[str, Adapter], title: str) -> str:
    rows = []
    for platform in prop.platforms:
        items = "".join(
            f"<li>{m.request.qty} × <a href='{escape(m.offer.url)}' target='_blank' rel='noopener'>{escape(m.offer.name)}</a> "
            f"@ {m.offer.price:,.2f}{'' if m.exact else ' <span class=close>close match</span>'}</li>"
            for _, m in sorted(prop.assignments.items()) if m.offer.platform == platform
        )
        rows.append(f"<div><b>{escape(label(settings, platform))}</b> {_egp(prop.subtotals[platform])} + fees {_egp(prop.fees[platform])}<ul>{items}</ul></div>")
    links = "".join(f"<div><a href='{escape(u)}' target='_blank' rel='noopener'>Open {escape(label(settings, p))} cart</a></div>"
                    for p, u in cart_links(result, prop, adapters).items())
    missing = ("<div class=warn>Not found: " + escape("; ".join(result.items[i].request.query for i in prop.missing)) + "</div>") if prop.missing else ""
    return f"<div class=plan><div>{title}: <b>{_egp(prop.total)}</b></div>{''.join(rows)}{links}{missing}</div>"


def _button(action: str | None, q: str, text: str, fields: dict, cls: str = "link") -> str:
    if not action:
        return ""
    hidden = "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in fields.items())
    return f"<form method=post action='{escape(action)}{q}' class=inline>{hidden}<button class={cls}>{escape(text)}</button></form>"


def _unit(o) -> str:
    return f"{o.unit_price:,.1f}/{_per(o.size.unit)}" if o.unit_price else ""


def _cards_html(result: CompareResult, settings: Settings, i: int, base: str | None, q: str) -> str:
    r = result.items[i]
    cards = []
    for j, c in enumerate(r.cards):
        best = c.cheapest()
        listings = "".join(f"<li>{escape(label(settings, p))}: <a href='{escape(o.url)}' target='_blank' rel='noopener'>"
                           f"{escape(o.name)}</a> · {o.price:,.2f}</li>" for p, o in c.per_platform().items())
        tag = "" if c.exact else " <span class=close>other size or wording</span>"
        cards.append(f"<div class=card><div><b>{escape(c.label)}</b>{tag}</div>"
                     f"<div class=price>from {best.price:,.2f} <span class=muted>{_unit(best)}</span></div><ul>{listings}</ul>"
                     f"{_button(base and base + '/pick', q, 'This one', {'item': i, 'card': j}, 'pick')}</div>")
    return f"<div class=cards>{''.join(cards)}</div>"


def to_html(result: CompareResult, settings: Settings, adapters: dict[str, Adapter],
            base: str | None = None, token: str | None = None, learned: dict[str, int] | None = None) -> str:
    """``base`` (like "/r/<id>") turns on the pick / not this / change buttons."""
    q = f"?t={escape(token)}" if token else ""
    plats = result.platforms
    head = "".join(f"<th>{escape(label(settings, p))}</th>" for p in plats)
    body_rows = []
    for i, r in enumerate(result.items):
        found = [m for m in r.matches.values() if m]
        pool = [m for m in found if m.exact] or found  # a cheaper wrong size is not "cheapest"
        cheapest = min(pool, key=lambda m: m.offer.price).offer.platform if pool else None
        cells = []
        for p in plats:
            m = r.matches.get(p)
            if not m:
                cells.append("<td class=muted>—</td>")
                continue
            o = m.offer
            unit = f"<div class=muted>{_unit(o)}</div>" if o.unit_price else ""
            deal = f" <s class=muted>{o.regular_price:,.2f}</s>" if o.on_deal else ""
            close = "<div class=muted>your pick</div>" if m.pinned else "" if m.exact else "<div class=close>close match</div>"
            no = "" if m.pinned else _button(base and base + "/reject", q, "not this", {"item": i, "platform": p})
            cells.append(f"<td class='{'best' if p == cheapest else ''}'><div class=price>{o.price:,.2f}{deal}</div>"
                         f"<div class=name><a href='{escape(o.url)}' target='_blank' rel='noopener'>{escape(o.name)}</a></div>{unit}{close}{no}</td>")
        state = ""
        if r.pinned:
            state = "<div class=muted>your product</div>" + _button(base and base + "/forget", q, "change", {"item": i})
        elif r.needs_pick:
            state = f"<div class=warn><a href='#pick-{i}'>choose your product</a></div>"
        body_rows.append(f"<tr><td><b>{r.request.qty} ×</b> {escape(r.request.query)}{state}</td>{''.join(cells)}</tr>")

    plans = ""
    pending = [i for i, r in enumerate(result.items) if r.needs_pick]
    if pending:
        plans += (f"<p class=warn>Not final: several products fit {len(pending)} line(s). Choose yours below; "
                  "shopper remembers it for next time.</p>")
    if result.best:
        plans += _plan_html(result, result.best, settings, adapters, "Best plan")
        if result.single and result.single is not result.best:
            plans += _plan_html(result, result.single, settings, adapters, "Best single platform")
    else:
        plans = "<p>No platform had an acceptable match.</p>"
    tips = [(r, tip_line(settings, r)) for r in result.items if r.tip]
    if tips:
        plans += "<div class=plan><div>Better-value packs</div><ul>" + "".join(
            f"<li>{escape(r.request.query)}: <a href='{escape(r.tip.offer.url)}' target='_blank' rel='noopener'>"
            f"{escape(line)}</a></li>" for r, line in tips) + "</ul></div>"
    picks = ""
    for i, r in enumerate(result.items):
        if not r.cards or r.pinned:
            continue
        cards = _cards_html(result, settings, i, base, q)
        title = f"<h3 id=pick-{i}>{escape(r.request.query)}</h3>"
        picks += (title + cards) if r.needs_pick else (
            f"<details id=pick-{i}><summary>{escape(r.request.query)}: wrong product? Choose</summary>{cards}</details>")
    if picks:
        picks = "<h2>Choose your products</h2>" + picks
    notes = []
    if result.unverified_fees:
        notes.append("Fees not verified (counted as 0): " + ", ".join(label(settings, p) for p in result.unverified_fees))
    notes += [f"{label(settings, p)}: {e}" for p, e in result.errors.items()]
    notes += [f"{label(settings, p)} prices are from its catalogue snapshot of {t}." for p, t in snapshot_ages(result).items()]
    notes_html = "".join(f"<p class=warn>{escape(n)}</p>" for n in notes)
    memo = (f"<p class=muted>Learned so far: {learned['lines']} chosen products, {learned['rejects']} rejected listings, "
            f"{learned['avoid']} avoided words.</p>") if learned else ""
    when = result.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    body = (f"<h1>Basket comparison</h1><div class=muted>{escape(settings.area_name)} · prices fetched {when} · "
            f"green = cheapest exact match per item · check the total in the app before paying</div>"
            f"<h2>Proposal</h2>{plans}{notes_html}{picks}<h2>All prices</h2>"
            f"<div class=wrap><table><thead><tr><th>Item</th>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>{memo}")
    return page("Basket comparison", body)


def debug_text(result: CompareResult, settings: Settings, top: int = 3) -> str:
    """Per platform: how many offers came back and how the best few scored."""
    from .matching import ACCEPT_SCORE, score

    out = []
    for r in result.items:
        out.append(f"\n## {r.request.qty} × {r.request.query}")
        for p in result.platforms:
            offers = r.candidates.get(p)
            if offers is None:
                out.append(f"  {label(settings, p)}: no response ({result.errors.get(p, 'not searched')})")
                continue
            ranked = sorted((score(r.request, o) for o in offers), key=lambda m: -m.score)[:top]
            out.append(f"  {label(settings, p)}: {len(offers)} offers")
            out += [f"     {m.score:5.1f} {'EXACT' if m.exact else 'close' if m.score >= ACCEPT_SCORE else 'below cut-off'}"
                    f" {m.offer.price:>9,.2f}  {m.offer.name}" for m in ranked]
    return "\n".join(out)
