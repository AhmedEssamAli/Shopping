"""Render a CompareResult as chat text (Telegram/CLI) or as an HTML comparison table."""
from __future__ import annotations

from html import escape

from .adapters import Adapter
from .config import Settings
from .models import Proposal
from .memory import offer_key
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


def _no_plan(result: CompareResult) -> str:
    if any(m for r in result.items for m in r.matches.values()):
        return ("No plan meets the shops' minimum orders. The prices are below; add items or allow more "
                "orders to get a plan.")
    return "No shop had an acceptable match for this list."


def _plan_lines(result: CompareResult, prop: Proposal, settings: Settings) -> list[str]:
    lines = []
    for platform in prop.platforms:
        lines.append(f"{label(settings, platform)} — {_egp(prop.subtotals[platform])} + fees {_egp(prop.fees[platform])}")
        for i, m in sorted(prop.assignments.items()):
            if m.offer.platform != platform:
                continue
            tag = "" if m.exact else " [close match]"
            if result.items[i].needs_pick:
                tag += " [choose your product]"
            lines.append(f"  • {m.request.qty} × {m.offer.name} @ {m.offer.price:,.2f}{tag}\n    {m.offer.url}")
    return lines


def to_text(result: CompareResult, settings: Settings, adapters: dict[str, Adapter], table_url: str | None = None) -> str:
    """Plain text for the CLI. Telegram uses ``to_chat``."""
    n = len(result.items)
    out = [f"{n} item{'s' if n != 1 else ''} · {settings.area_name} · searched {', '.join(label(settings, p) for p in result.platforms)}"]
    best = result.best
    if not best:
        out.append(_no_plan(result))
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
        out.append(f"Problem with {label(settings, p)}: {err}")
    out.append(f"Prices fetched {result.created_at.astimezone().strftime('%H:%M')}. Check the total in the app before paying.")
    out += [f"{label(settings, p)} prices are from its catalogue snapshot of {t}." for p, t in snapshot_ages(result).items()]
    if table_url:
        out.append(f"\nFull table: {table_url}")
    return "\n".join(out)


# Design: a shop's shelf edge. Prices sit on shelf labels, the cheapest exact one on the
# yellow "best price" label that supermarkets use; everything else stays quiet basil and white.
FONTS = ("<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
         "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700"
         "&family=Schibsted+Grotesk:wght@400;500;700;800&display=swap'>")

def to_chat(result: CompareResult, settings: Settings, adapters: dict[str, Adapter], table_url: str | None = None) -> str:
    """The result for Telegram, in its HTML subset: product names are links, no bare URLs.
    Each line is complete on its own, so splitting a long message at line breaks is safe."""
    e = escape
    best = result.best
    n = len(result.items)
    if not best:
        out = [e(_no_plan(result))]
    else:
        orders = "1 order" if len(best.platforms) == 1 else f"{len(best.platforms)} orders"
        out = [f"<b>Best plan: {_egp(best.total)}</b>, {orders}"]
        pending = [r.request.query for r in result.items if r.needs_pick]
        if pending:
            out.append(f"<b>Not final.</b> Choose the product for {e('; '.join(pending))} with the buttons below.")
        for platform in best.platforms:
            out.append(f"\n<b>{e(label(settings, platform))}</b>  {_egp(best.subtotals[platform] + best.fees[platform])}"
                       f" (fees {best.fees[platform]:,.2f})")
            for i, m in sorted(best.assignments.items()):
                if m.offer.platform != platform:
                    continue
                note = "" if m.exact else " <i>close match</i>"
                if result.items[i].needs_pick:
                    note += " <i>choose below</i>"
                out.append(f"• {m.request.qty} × <a href=\"{e(m.offer.url)}\">{e(m.offer.name)}</a> — {m.line_total:,.2f}{note}")
        single = result.single
        if single and single is not best:
            diff = single.total - best.total
            out.append(f"\nOne shop only: {e(label(settings, single.platforms[0]))} {_egp(single.total)} "
                       f"({'+' if diff >= 0 else ''}{diff:,.2f})")
        if best.missing:
            out.append("Not found anywhere: " + e("; ".join(result.items[i].request.query for i in best.missing)))
        for r in result.items:
            if r.tip:
                out.append(f"Better value: <a href=\"{e(r.tip.offer.url)}\">{e(tip_line(settings, r))}</a>")
    if result.unverified_fees:
        out.append("\nFees not counted yet: " + e(", ".join(label(settings, p) for p in result.unverified_fees)))
    for p, err in result.errors.items():
        out.append(f"Problem with {e(label(settings, p))}: {e(err)}")
    when = result.created_at.astimezone().strftime("%H:%M")
    out.append(f"Prices from {when}, {len(result.platforms)} shops, {n} item{'s' if n != 1 else ''}. "
               "Check the total in the app before you pay.")
    if table_url:
        out.append(f'<a href="{e(table_url)}">Open the full table</a>')
    return "\n".join(out)


CSS = """
:root{--page:#f4f7f2;--card:#fff;--ink:#1a2b21;--muted:#5d6b62;--line:#dce5da;--basil:#1e5b3a;--basil-ink:#fff;
--leaf:#e3efe1;--tag:#ffd43b;--tag-ink:#1a2b21;--tomato:#c8372d;--tomato-bg:#fdecea;--focus:#2f7fd8;
--ui:'Schibsted Grotesk',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--num:'Barlow Condensed','Arial Narrow',var(--ui)}
@media (prefers-color-scheme:dark){:root{--page:#0f1913;--card:#16241c;--ink:#e6efe8;--muted:#9db0a3;--line:#253a2d;
--basil:#2d7a50;--leaf:#1b2e22;--tomato:#ff8a7a;--tomato-bg:#3a1d1a}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);font:16px/1.5 var(--ui)}
a{color:inherit;text-underline-offset:2px}:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.top{background:var(--basil);color:var(--basil-ink)}.top .in{max-width:1040px;margin:0 auto;padding:22px 16px 26px}
.top h1{font-size:28px;line-height:1.15;font-weight:800;margin:0;letter-spacing:-.01em}
.top p{margin:6px 0 0;opacity:.85;font-size:14px}.top a{color:inherit}
main{max-width:1040px;margin:0 auto;padding:0 16px 48px}
h2{font-size:20px;font-weight:800;margin:32px 0 10px;letter-spacing:-.01em}h3{font-size:17px;font-weight:700;margin:0}
.muted{color:var(--muted)}.small{font-size:13px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px}
.plan{margin-top:-14px;box-shadow:0 6px 24px rgba(30,91,58,.12)}
.plan .head{display:flex;flex-wrap:wrap;align-items:center;gap:12px 16px}.plan .head h2{margin:0;flex:1 1 auto}
.tag{display:inline-flex;align-items:baseline;gap:4px;background:var(--tag);color:var(--tag-ink);font-family:var(--num);
font-weight:700;border-radius:4px;padding:2px 10px;line-height:1.1;font-variant-numeric:tabular-nums}
.tag small{font-size:.5em;font-weight:600}.tag.big{font-size:40px;padding:4px 14px}
.bag{border-top:1px solid var(--line);margin-top:14px;padding-top:12px}
.bag .row{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.bag ul{list-style:none;margin:8px 0 0;padding:0}.bag li{display:flex;justify-content:space-between;gap:12px;padding:5px 0;font-size:15px}
.bag li span:last-child,.num{font-family:var(--num);font-size:18px;font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
.alt{margin-top:12px;font-size:14px}
.notice{border-left:4px solid var(--tomato);background:var(--tomato-bg);border-radius:4px 12px 12px 4px;padding:12px 14px;margin:18px 0 0}
.notice b{color:var(--tomato)}
.shops{list-style:none;padding:0;margin:0;display:grid;gap:8px}
.shops li{display:grid;grid-template-columns:minmax(110px,1.2fr) 3fr auto;gap:12px;align-items:center;font-size:14px}
.bar{height:10px;border-radius:5px;background:var(--leaf);overflow:hidden}.bar i{display:block;height:100%;background:var(--basil)}
.shops .best .bar i{background:var(--tag)}.shops .partial .bar i{background:repeating-linear-gradient(135deg,var(--muted) 0 4px,transparent 4px 8px);opacity:.5}
.line{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin:12px 0}
.line>header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-bottom:12px}
.qty{font-family:var(--num);font-weight:700;font-size:20px;color:var(--basil)}
.state{font-size:13px}.state.pick{color:var(--tomato);font-weight:700}
.labels{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.label{border:1px solid var(--line);border-top:4px solid var(--basil);border-radius:4px;padding:8px 10px 10px;background:var(--card)}
.label.best{background:var(--tag);border-color:var(--tag);color:var(--tag-ink)}.label.best .muted{color:#4d4a2a}
.label.close{border-style:dashed;border-top-style:solid;border-top-color:var(--line)}
.label .shop{font-size:12px;font-weight:700}.label .amount{font-family:var(--num);font-size:30px;font-weight:700;line-height:1.05}
.label .product{display:block;font-size:13px;line-height:1.3;margin-top:4px}
.flag{font-size:12px;margin-top:4px}.flag.close{color:var(--tomato)}
.missing{font-size:13px;margin:10px 0 0}
img.thumb,.thumb.nophoto{display:block;width:100%;height:84px;object-fit:contain;background:#fff;border-radius:4px;margin:0 0 6px}
.thumb.nophoto{background:repeating-linear-gradient(135deg,var(--leaf) 0 6px,transparent 6px 12px);opacity:.6}
img.mini{width:36px;height:36px;object-fit:contain;background:#fff;border-radius:4px;flex:none}
.withimg{display:flex;gap:10px;align-items:center}
.choices{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(230px,1fr);gap:10px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
.choice{scroll-snap-align:start;border:1px solid var(--line);border-radius:12px;padding:14px;background:var(--card);display:flex;flex-direction:column;gap:6px}
.choice ul{margin:0;padding-left:18px;font-size:13px}.choice .push{margin-top:auto}
details.choose{margin:10px 0 0}details.choose summary{cursor:pointer;font-size:14px;color:var(--muted)}
form.inline{display:inline;margin:0}
button{font:inherit;font-weight:700;border:0;border-radius:10px;padding:10px 16px;background:var(--basil);color:var(--basil-ink);cursor:pointer}
button.link{background:none;color:inherit;padding:2px 0;font-weight:500;font-size:12px;text-decoration:underline;opacity:.75}
.notes{margin-top:28px;font-size:13px}.notes p{margin:4px 0}.warn{color:var(--tomato)}
textarea{width:100%;min-height:180px;font:inherit;padding:12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
input[type=number]{font:inherit;width:4em;padding:4px 6px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
code{font-size:.9em;background:var(--leaf);padding:1px 5px;border-radius:4px}
.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px}button.quiet{background:var(--leaf);color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:var(--card);color:var(--ink);border:1px solid var(--line);
border-radius:999px;padding:6px 12px;font-weight:500;font-size:14px}.chip:disabled{opacity:.45}
ul.list{list-style:none;margin:0;padding:0}ul.list li{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
padding:8px 0;border-bottom:1px solid var(--line)}ul.list li:last-child{border-bottom:0}
@media (max-width:520px){.top h1{font-size:24px}.tag.big{font-size:34px}.shops li{grid-template-columns:1fr auto}.shops .bar{grid-column:1/-1;order:3}}
@media (prefers-reduced-motion:no-preference){.label.best{animation:pop .5s ease-out 1}@keyframes pop{from{transform:scale(.96)}to{transform:none}}}
"""


def page(title: str, body: str, header: str = "") -> str:
    """``header``: inner HTML of the basil top band (title and summary)."""
    top = f"<div class=top><div class=in>{header}</div></div>" if header else ""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title>{FONTS}<style>{CSS}</style></head><body>{top}<main>{body}</main></body></html>")


def _button(action: str | None, q: str, text: str, fields: dict, cls: str = "link") -> str:
    if not action:
        return ""
    hidden = "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in fields.items())
    return f"<form method=post action='{escape(action)}{q}' class=inline>{hidden}<button class={cls}>{escape(text)}</button></form>"


def _unit(o) -> str:
    return f"{o.unit_price:,.1f}/{_per(o.size.unit)}" if o.unit_price else ""


def _tag(amount: float, big: bool = False) -> str:
    return f"<span class='tag{' big' if big else ''}'><small>EGP</small>{amount:,.2f}</span>"


def _img(o, cls: str = "thumb") -> str:
    """The product photo, loaded from the shop; nothing when the shop gave none."""
    if not getattr(o, "image", ""):
        return ""
    return (f"<img class={cls} src='{escape(o.image)}' alt='' loading=lazy decoding=async referrerpolicy=no-referrer "
            "onerror=\"this.remove()\">")


def _link(o, cls: str = "") -> str:
    return f"<a class='{cls}' href='{escape(o.url)}' target='_blank' rel='noopener'>{escape(o.name)}</a>"


def _plan_html(result: CompareResult, prop: Proposal, settings: Settings, adapters: dict[str, Adapter]) -> str:
    shops = " and ".join(label(settings, p) for p in prop.platforms)
    split = "one order" if len(prop.platforms) == 1 else f"{len(prop.platforms)} orders"
    bags = []
    for platform in prop.platforms:
        items = "".join(
            f"<li><span class=withimg>{_img(m.offer, 'mini')}<span>{m.request.qty} × {_link(m.offer)}"
            f"{'' if m.exact else ' <span class=warn>close match</span>'}</span></span>"
            f"<span>{m.line_total:,.2f}</span></li>"
            for _, m in sorted(prop.assignments.items()) if m.offer.platform == platform)
        fee = prop.fees[platform]
        bags.append(f"<div class=bag><div class=row><h3>{escape(label(settings, platform))}</h3>"
                    f"<span class=num>{prop.subtotals[platform] + fee:,.2f}</span></div>"
                    f"<div class='muted small'>items {prop.subtotals[platform]:,.2f}, fees {fee:,.2f}</div><ul>{items}</ul></div>")
    links = "".join(f"<p class=small><a href='{escape(u)}' target='_blank' rel='noopener'>Open the {escape(label(settings, p))} cart</a></p>"
                    for p, u in cart_links(result, prop, adapters).items())
    missing = ("<p class='warn small'>Not found anywhere: " + escape("; ".join(result.items[i].request.query for i in prop.missing))
               + "</p>") if prop.missing else ""
    return (f"<div class=head><h2>Best plan</h2>{_tag(prop.total, big=True)}</div>"
            f"<p class='muted small'>{escape(shops)}, {split}</p>{''.join(bags)}{links}{missing}<!--cart-push-->")


def _one_shop_html(result: CompareResult, settings: Settings) -> str:
    """What the whole basket costs at each shop on its own: the honest comparison."""
    singles = sorted((p for p in result.proposals if len(p.platforms) == 1), key=lambda p: (len(p.missing), p.total))
    if len(singles) < 2:
        return ""
    top = max(p.total for p in singles) or 1
    n = len(result.items)
    rows = []
    for k, p in enumerate(singles):
        have = n - len(p.missing)
        note = "all items" if not p.missing else f"{have} of {n} items"
        rows.append(f"<li class='{'best' if k == 0 else 'partial' if p.missing else ''}'><span><b>{escape(label(settings, p.platforms[0]))}</b>"
                    f"<br><span class='muted small'>{note}</span></span><span class=bar><i style='width:{p.total / top * 100:.0f}%'></i></span>"
                    f"<span class=num>{p.total:,.2f}</span></li>")
    return f"<h2>If you buy everything in one shop</h2><div class=panel><ul class=shops>{''.join(rows)}</ul></div>"


def _cards_html(result: CompareResult, settings: Settings, i: int, base: str | None, q: str) -> str:
    r = result.items[i]
    cards = []
    for j, c in enumerate(r.cards):
        best = c.cheapest()
        listings = "".join(f"<li>{escape(label(settings, p))}: {_link(o)}, {o.price:,.2f}</li>" for p, o in c.per_platform().items())
        tag = "" if c.exact else "<span class='warn small'>other size or wording</span>"
        pic = next((o for o in c.offers if getattr(o, "image", "")), None)
        cards.append(f"<div class=choice>{_img(pic) if pic else ''}<h3>{escape(c.label)}</h3>{tag}<div>{_tag(best.price)} <span class='muted small'>{_unit(best)}</span></div>"
                     f"<ul>{listings}</ul><div class=push>{_button(base and base + '/pick', q, 'This one', {'item': i, 'card': c.key}, 'pick')}</div></div>")
    return f"<div class=choices>{''.join(cards)}</div>"


def _line_html(result: CompareResult, settings: Settings, i: int, base: str | None, q: str) -> str:
    r = result.items[i]
    found = [(p, m) for p, m in r.matches.items() if m]
    exact = [m for _, m in found if m.exact]
    cheapest = min(exact or [m for _, m in found], key=lambda m: m.offer.price, default=None)
    found.sort(key=lambda pm: (not pm[1].exact, pm[1].offer.price))
    labels = []
    for p, m in found:
        o = m.offer
        cls = "best" if m is cheapest else "" if m.exact else "close"
        flag = ("your pick" if m.pinned else "" if m.exact else "close match")
        was = f"was {o.regular_price:,.2f}" if o.on_deal else ""
        flags = ", ".join(x for x in (flag, was) if x)
        no = "" if m.pinned else _button(base and base + "/reject", q, "not this",
                                         {"item": i, "platform": p, "offer": offer_key(o)})
        pic = _img(o) or "<div class='thumb nophoto'></div>"
        labels.append(f"<li class='label {cls}'>{pic}<div class=shop>{escape(label(settings, p))}</div>"
                      f"<div class=amount>{o.price:,.2f}</div><div class='muted small'>{_unit(o)}</div>{_link(o, 'product')}"
                      f"<div class='flag {'close' if not m.exact else ''}'>{escape(flags)}</div>{no}</li>")
    missing = [label(settings, p) for p in result.platforms if not r.matches.get(p)]
    state = ""
    if r.pinned:
        state = "<span class='state muted'>your product</span>" + _button(base and base + "/forget", q, "change", {"item": i})
    elif r.needs_pick:
        state = f"<a class='state pick' href='#pick-{i}'>choose your product</a>"
    body = f"<ul class=labels>{''.join(labels)}</ul>" if labels else "<p class=muted>No shop had this item.</p>"
    miss = f"<p class='missing muted'>Not found at {escape(', '.join(missing))}</p>" if labels and missing else ""
    choose = ""
    if r.cards and not r.pinned:
        cards = _cards_html(result, settings, i, base, q)
        choose = (f"<div id=pick-{i}><h3 class=small style='margin:16px 0 8px'>Which one do you mean?</h3>{cards}</div>"
                  if r.needs_pick else f"<details class=choose id=pick-{i}><summary>Wrong product? Choose</summary>{cards}</details>")
    return (f"<section class=line><header><span class=qty>{r.request.qty}×</span><h3>{escape(r.request.query)}</h3>{state}</header>"
            f"{body}{miss}{choose}</section>")


def to_html(result: CompareResult, settings: Settings, adapters: dict[str, Adapter],
            base: str | None = None, token: str | None = None, learned: dict[str, int] | None = None) -> str:
    """``base`` (like "/r/<id>") turns on the pick / not this / change buttons."""
    q = f"?t={escape(token)}" if token else ""
    n = len(result.items)
    when = result.created_at.astimezone().strftime("%d %b, %H:%M")
    header = (f"<h1>Basket comparison</h1><p>{n} item{'s' if n != 1 else ''} for {escape(settings.area_name)}, "
              f"{len(result.platforms)} shops searched, prices from {when}. Check the total in the app before you pay.</p>")
    pending = [r for r in result.items if r.needs_pick]
    plan = (_plan_html(result, result.best, settings, adapters) if result.best
            else f"<h2>No plan yet</h2><p class=muted>{escape(_no_plan(result))}</p>")
    notice = (f"<p class=notice><b>Not final.</b> Several products fit {len(pending)} line{'s' if len(pending) != 1 else ''}. "
              "Choose yours below; shopper remembers it for next time.</p>") if pending else ""
    tips = [(r, tip_line(settings, r)) for r in result.items if r.tip]
    tips_html = ("<h2>Better-value packs</h2><div class=panel><ul>" + "".join(
        f"<li>{escape(r.request.query)}: <a href='{escape(r.tip.offer.url)}' target='_blank' rel='noopener'>{escape(line)}</a></li>"
        for r, line in tips) + "</ul></div>") if tips else ""
    lines = "".join(_line_html(result, settings, i, base, q) for i in range(n))
    notes = []
    if result.unverified_fees:
        notes.append("Fees not verified, counted as 0: " + ", ".join(label(settings, p) for p in result.unverified_fees))
    notes += [f"{label(settings, p)}: {e}" for p, e in result.errors.items()]
    notes += [f"{label(settings, p)} prices are from its catalogue snapshot of {t}." for p, t in snapshot_ages(result).items()]
    notes += [f"{a.label} prices are for its {a.branch} branch, the one that serves your location (AREA_LAT, AREA_LON)."
              for p, a in adapters.items() if p in result.platforms and getattr(a, "branch", None)]
    if learned:
        notes.append(f"Learned so far: {learned['lines']} chosen product{'s' if learned['lines'] != 1 else ''}, "
                     f"{learned['rejects']} rejected listing{'s' if learned['rejects'] != 1 else ''}, "
                     f"{learned['avoid']} avoided word{'s' if learned['avoid'] != 1 else ''}.")
    notes_html = "<div class='notes muted'>" + "".join(f"<p>{escape(x)}</p>" for x in notes) + "</div>" if notes else ""
    body = (f"<div class='panel plan'>{plan}</div>{notice}{_one_shop_html(result, settings)}{tips_html}"
            f"<h2>{'Choose your products and compare' if pending else 'Every price, item by item'}</h2>{lines}{notes_html}")
    return page("Basket comparison", body, header)


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
