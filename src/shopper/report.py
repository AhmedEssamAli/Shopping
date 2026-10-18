"""Render a CompareResult as chat text (Telegram/CLI) or as an HTML comparison table."""
from __future__ import annotations

from html import escape

from .adapters import Adapter
from .config import Settings
from .models import Proposal
from .service import CompareResult


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
    if result.unverified_fees:
        out.append("\nFees not verified (counted as 0): " + ", ".join(label(settings, p) for p in result.unverified_fees))
    for p, err in result.errors.items():
        out.append(f"⚠ {label(settings, p)}: {err}")
    out.append(f"Prices fetched {result.created_at.astimezone().strftime('%H:%M')}. Check the total in the app before paying.")
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


def to_html(result: CompareResult, settings: Settings, adapters: dict[str, Adapter]) -> str:
    plats = result.platforms
    head = "".join(f"<th>{escape(label(settings, p))}</th>" for p in plats)
    body_rows = []
    for r in result.items:
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
            unit = f"<div class=muted>{o.unit_price:,.1f}/{'L' if o.size.unit == 'ml' else 'kg' if o.size.unit == 'g' else 'pc'}</div>" if o.unit_price else ""
            deal = f" <s class=muted>{o.regular_price:,.2f}</s>" if o.on_deal else ""
            close = "" if m.exact else "<div class=close>close match</div>"
            cells.append(f"<td class='{'best' if p == cheapest else ''}'><div class=price>{o.price:,.2f}{deal}</div>"
                         f"<div class=name><a href='{escape(o.url)}' target='_blank' rel='noopener'>{escape(o.name)}</a></div>{unit}{close}</td>")
        body_rows.append(f"<tr><td><b>{r.request.qty} ×</b> {escape(r.request.query)}</td>{''.join(cells)}</tr>")

    plans = ""
    if result.best:
        plans += _plan_html(result, result.best, settings, adapters, "Best plan")
        if result.single and result.single is not result.best:
            plans += _plan_html(result, result.single, settings, adapters, "Best single platform")
    else:
        plans = "<p>No platform had an acceptable match.</p>"
    notes = []
    if result.unverified_fees:
        notes.append("Fees not verified (counted as 0): " + ", ".join(label(settings, p) for p in result.unverified_fees))
    notes += [f"{label(settings, p)}: {e}" for p, e in result.errors.items()]
    notes_html = "".join(f"<p class=warn>{escape(n)}</p>" for n in notes)
    when = result.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    body = (f"<h1>Basket comparison</h1><div class=muted>{escape(settings.area_name)} · prices fetched {when} · "
            f"green = cheapest exact match per item · check the total in the app before paying</div>"
            f"<h2>Proposal</h2>{plans}{notes_html}<h2>All prices</h2>"
            f"<div class=wrap><table><thead><tr><th>Item</th>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>")
    return page("Basket comparison", body)
